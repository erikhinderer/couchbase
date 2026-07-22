# Claude Desktop: Couchbase Data Management

Shows how you can query and manage Couchbase data through conversation. Browse buckets/scopes/collections, run SQL++ (N1QL) queries, inspect schemas, and check cluster health. All within a single ChatBot conversation.

Check out [demo-prompts.md](demo-prompts.md) for commands you can copy-paste to show off the Couchbase MCP server.

## What You Need
- Claude Desktop installed
- A Couchbase cluster (a free [Capella](https://cloud.couchbase.com/sign-up) cluster, or the `couchbase` service from the root `docker-compose.yml`)

## Setup: Couchbase Data Operations

The official [Couchbase MCP server](https://github.com/couchbase/mcp-server-couchbase) exposes cluster health, schema discovery, KV document, SQL++ query, and query-performance tools.

### Install
The server ships on PyPI, so `uvx` can run it directly - no clone needed:

```bash
uvx couchbase-mcp-server --version
```

(If you'd rather run from source: `git clone https://github.com/couchbase/mcp-server-couchbase.git`, then use the "Running from Source" config below.)

### Get Your Couchbase Info
1. If using Capella, open the [Capella UI](https://cloud.couchbase.com) and copy your cluster's connection string
2. Note your database username/password (create one under **Settings > Database Access** for Capella)
3. If using the local Docker Compose stack, the connection string is `couchbase://localhost` with the credentials set in `.env` (`COUCHBASE_USERNAME` / `COUCHBASE_PASSWORD`, defaulting to `Administrator` / `CouchbaseDemo123!`)

### Configure Claude Desktop
Find your config file:
- **Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add this (replace the placeholder values):
```json
{
  "mcpServers": {
    "couchbase": {
      "command": "uvx",
      "args": ["couchbase-mcp-server"],
      "env": {
        "CB_CONNECTION_STRING": "couchbases://your-cluster-address",
        "CB_USERNAME": "your_username",
        "CB_PASSWORD": "your_actual_password",
        "CB_MCP_READ_ONLY_MODE": "true"
      }
    }
  }
}
```

**Update these:**
- `couchbases://your-cluster-address` - your Couchbase connection string (`couchbase://localhost` for the local Docker Compose stack, without the trailing `s`)
- `your_username` / `your_actual_password` - your Couchbase credentials
- `CB_MCP_READ_ONLY_MODE` - set to `"false"` if you want the LLM to be able to write/delete documents, not just read

#### Running from Source instead of PyPI
```json
{
  "mcpServers": {
    "couchbase": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/yourname/path/to/mcp-server-couchbase/",
        "run",
        "src/mcp_server.py"
      ],
      "env": {
        "CB_CONNECTION_STRING": "couchbases://your-cluster-address",
        "CB_USERNAME": "your_username",
        "CB_PASSWORD": "your_actual_password"
      }
    }
  }
}
```

## A Note on Cluster/Database Provisioning

Redis has a separate `mcp-redis-cloud` server for creating and managing Redis Cloud databases via chat. There isn't a published equivalent MCP server for provisioning Capella clusters as of this writing - use the [Capella UI](https://cloud.couchbase.com) or the [Capella management REST API](https://docs.couchbase.com/cloud/management-api-reference/index.html) directly for cluster/bucket provisioning, and the Couchbase MCP server above for querying and managing data once a cluster exists.

## Test Everything Works

1. Quit Claude Desktop completely
2. Start it again
3. Open a new chat
4. Try the prompts in [demo-prompts.md](demo-prompts.md)
