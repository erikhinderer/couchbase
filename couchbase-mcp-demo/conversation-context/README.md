# Conversation Context Demo

This demo shows how LLMs can directly read and write Couchbase data through natural language using MCP.

## Simple Example of What It Does

- **LLM stores data:** Tell the AI "My name is Yusuf, I like to eat Oranges". It writes to Couchbase automatically
- **LLM retrieves data:** Ask "What's my name?". It reads from Couchbase and answers
- **Data persists:** Close the app, restart, ask again. Data survives in Couchbase
- **Watch it happen:** Use the Couchbase Server Web Console (or Capella UI) to see documents updating in real-time

## Demo Files

- `couchbase_mcp_showcase.py` - LLM with the Couchbase MCP server (simple)
- `without_mcp.py` - Traditional Couchbase SDK integration (complex)

## Setup Instructions

### 1. Get a Couchbase Cluster (Free)
- Go to https://cloud.couchbase.com/sign-up to spin up a free Capella cluster, or run Couchbase Server locally (see the root `docker-compose.yml`, which already provisions the `mcp_demo` bucket/scope used by the main demo)
- Copy: connection string, username, password
- Create a `chat_history` collection in the `mcp_demo` scope for this demo (the main tool-filtering demo only creates `tools` and `semantic_cache`)

### 2. Install Ollama
- All LLM inference in this demo runs locally through Ollama - no external API key needed
- Go to https://ollama.com to install it, then pull a model: `ollama pull llama3.1:8b`

### 3. Install the Couchbase MCP Server
The Couchbase MCP server ships on PyPI, so `uvx` can run it with no separate clone step:

```bash
uvx couchbase-mcp-server --version
```

If you'd rather run from source, clone https://github.com/couchbase/mcp-server-couchbase and set `mcp_source_path` in `config.py`.

### 4. Configure Demo
```bash
cd couchbase-mcp-demo/conversation-context
cp config.py.example config.py
# Edit config.py - add your Couchbase connection string and credentials
```

### 5. Run Demo

```bash
cd conversation-context
pip install -r requirements.txt
python3 couchbase_mcp_showcase.py
```

The Couchbase MCP server is launched automatically as a subprocess (via `uvx couchbase-mcp-server`) - no separate terminal needed.

## Quick Test

1. Say: "I like pizza"
2. **It remembers!** Check the Couchbase Web Console to see the new document in `mcp_demo.mcp_demo.chat_history`.
3. Exit and restart
4. Ask: "What do I like?"

## Key Insight

**Without MCP:** Developers write Couchbase connection code, document modeling, and N1QL queries by hand (see `without_mcp.py`).
**With MCP:** The LLM handles all Couchbase operations through conversation, using the Couchbase MCP server's document and SQL++ query tools. This enables any LLM application to have persistent memory powered by Couchbase with zero hand-written database code.
