import asyncio
from openai import AsyncOpenAI
from agents import Agent, Runner, set_default_openai_client, set_default_openai_api, set_tracing_disabled
from openai.types.responses import ResponseTextDeltaEvent
from agents.mcp import MCPServerStdio
from config import COUCHBASE_CONFIG, MCP_CONFIG, OLLAMA_CONFIG, get_mcp_path

# Point the Agents SDK at the local Ollama server instead of OpenAI's hosted
# API - no external API key needed. Ollama only speaks the Chat Completions
# shape, and tracing normally uploads to OpenAI's platform using a real API
# key, so both are adjusted here.
set_default_openai_client(
    AsyncOpenAI(base_url=OLLAMA_CONFIG["base_url"], api_key=OLLAMA_CONFIG["api_key"]),
    use_for_tracing=False,
)
set_default_openai_api("chat_completions")
set_tracing_disabled(True)


async def main():
    # Connect to the Couchbase MCP server. Runs the published PyPI package via
    # `uvx` by default; set MCP_CONFIG["mcp_source_path"] in config.py to run
    # from a local clone of https://github.com/couchbase/mcp-server-couchbase instead.
    mcp_path = get_mcp_path()
    server = MCPServerStdio(
        params={
            "command": MCP_CONFIG["command"],
            "args": ["--directory", mcp_path, "run", "src/mcp_server.py"] if mcp_path else ["couchbase-mcp-server"],
            "env": {
                "CB_CONNECTION_STRING": COUCHBASE_CONFIG["CB_CONNECTION_STRING"],
                "CB_USERNAME": COUCHBASE_CONFIG["CB_USERNAME"],
                "CB_PASSWORD": COUCHBASE_CONFIG["CB_PASSWORD"],
                "CB_MCP_READ_ONLY_MODE": "false",  # this demo needs to write conversation turns
            },
        }
    )
    await server.connect()

    # Local Ollama agent with the Couchbase MCP server as persistent memory
    agent = Agent(
        name="Couchbase AI",
        instructions=(
            "You have persistent memory powered by Couchbase. Store every conversation turn as a "
            "JSON document in the `chat_history` collection (bucket mcp_demo, scope mcp_demo), using "
            "a document ID like `chat::<unix_timestamp_ms>` with fields {role, content, timestamp}. "
            "When answering, first run a SQL++ query such as "
            "\"SELECT role, content FROM chat_history ORDER BY timestamp DESC LIMIT 20\" "
            "to retrieve recent history for context."
        ),
        mcp_servers=[server],
        model=OLLAMA_CONFIG["model"]
    )

    print("Couchbase MCP Demo (local Ollama model)\n")

    while True:     # chatbot loop
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        # runner for orchestrating the agent
        result = Runner.run_streamed(agent, user_input)

        # Print response
        print("AI: ", end="")
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                print(event.data.delta, end="", flush=True)
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
