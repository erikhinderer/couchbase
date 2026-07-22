# Couchbase Vector Search CLI

Natural language movie search powered by Couchbase vector search and MCP (Model Context Protocol).

## Loading Movie Data

Couchbase doesn't ship a pre-built movie-embeddings dataset loader the way Redis does.
Load your own movie documents (with a `plot_embedding` field generated using the
same model queried at runtime, `all-MiniLM-L6-v2` / 384 dimensions) into the
`movies` collection under the `mcp_demo` bucket / `mcp_demo` scope, then create
the Search vector index described in `index.txt`.

## Setup Instructions

### 1. Get a Couchbase Cluster (Free)
- Go to https://cloud.couchbase.com/sign-up for a free Capella cluster, or use the `couchbase` service from the root `docker-compose.yml`
- Copy: connection string, username, password

### 2. Install Ollama
- All LLM inference in this demo runs locally through Ollama - no external API key needed
- Go to https://ollama.com to install it, then pull a model: `ollama pull llama3.1:8b`

### 3. Install the Couchbase MCP Server
The Couchbase MCP server ships on PyPI, so `uvx` can run it with no separate clone step:

```bash
uvx couchbase-mcp-server --version
```

If you'd rather run from source, clone https://github.com/couchbase/mcp-server-couchbase and set `mcp_source_path` in `config.py`.

### 4. Load Data
See "Loading Movie Data" above - create the `movies` collection and the `movies-vector-index` Search vector index from `index.txt` before running the CLI.

### 5. Configure Demo
```bash
cd vector-search-mcp
cp config.py.example config.py
# Edit config.py with your Couchbase connection string and credentials
```

### 6. Run Demo

```bash
cd vector-search-mcp
pip install -r requirements.txt
python3 vector_search_cli.py
```

The Couchbase MCP server is launched automatically as a subprocess (via `uvx couchbase-mcp-server`) - no separate terminal needed.

## Usage

```
 You: Find space adventure movies with aliens
 AI: Here are some great space adventure films featuring aliens...

 You: Show me romantic comedies set in Paris
 AI: I found several romantic comedies set in Paris...
```

Example queries:
- "Find movies about time travel"
- "Search for films similar to Blade Runner"

Type `exit` or `quit` to close.

## How It Works

1. **MCP Connection**: Connects to Couchbase through the Couchbase MCP server
2. **AI Agent**: A local Ollama model interprets natural language queries
3. **Vector Search**: Couchbase's Search service performs semantic search on movie embeddings
4. **Natural Response**: AI formats results conversationally

The CLI demonstrates natural language interaction with Couchbase vector search without writing complex queries.
