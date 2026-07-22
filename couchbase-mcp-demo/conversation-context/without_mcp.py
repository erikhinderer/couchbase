import time
import json
import openai
from datetime import timedelta, datetime
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions
from couchbase.auth import PasswordAuthenticator
from config import COUCHBASE_CONFIG, OLLAMA_CONFIG

BUCKET = "mcp_demo"
SCOPE = "mcp_demo"
COLLECTION = "chat_history"

auth = PasswordAuthenticator(COUCHBASE_CONFIG["CB_USERNAME"], COUCHBASE_CONFIG["CB_PASSWORD"])
cluster = Cluster(COUCHBASE_CONFIG["CB_CONNECTION_STRING"], ClusterOptions(auth))
cluster.wait_until_ready(timedelta(seconds=10))
collection = cluster.bucket(BUCKET).scope(SCOPE).collection(COLLECTION)

# Point the openai client at the local Ollama server (its OpenAI-compatible
# API) instead of api.openai.com - no external API key needed.
client = openai.OpenAI(base_url=OLLAMA_CONFIG["base_url"], api_key=OLLAMA_CONFIG["api_key"])


# the function to store messages as documents in Couchbase (no Redis stream needed -
# each message is its own JSON document, ordered by its timestamp field)
def store_message(role, content):
    message_data = {
        'role': role,
        'content': content,
        'timestamp': datetime.now().isoformat()
    }
    doc_id = f"chat::{time.time_ns()}"
    collection.upsert(doc_id, message_data)


# function to retrieve conversation history from Couchbase via a N1QL query
def get_conversation_history():
    query = f"""
        SELECT role, content
        FROM `{BUCKET}`.`{SCOPE}`.`{COLLECTION}`
        ORDER BY timestamp DESC
        LIMIT 20
    """
    result = cluster.query(query)
    messages = [f"{row['role']}: {row['content']}" for row in result.rows()]
    # Reverse back into chronological order for the LLM prompt
    return "\n".join(reversed(messages))


### function to send user input to the local Ollama model and get a response
def chat_with_ai(user_input, history):
    messages = [
        {"role": "system",
          "content": "You are a helpful assistant. Use the conversation history for context."},
        {"role": "user",
         "content": f"Conversation history:\n{history}\n\nUser: {user_input}"}
    ]

    response = client.chat.completions.create(
        model=OLLAMA_CONFIG["model"],
        messages=messages
    )
    return response.choices[0].message.content

def main():
    print(" Non-MCP Couchbase Demo.")

    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        store_message("user", user_input)

        history = get_conversation_history()

        ai_response = chat_with_ai(user_input, history)

        store_message("assistant", ai_response)

        print(f"AI: {ai_response}\n")


if __name__ == "__main__":
    main()
