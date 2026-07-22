# Couchbase MCP Tool Filtering Demo App

FastAPI app for the Couchbase-branded MCP tool filtering demo. It uses:

- Couchbase Search (FTS) vector indexes for tool vector indexing and semantic cache lookup, plus Couchbase KV/N1QL for document storage
- SentenceTransformers for local embeddings
- A local Ollama model, via its OpenAI-compatible `/v1/chat/completions` API, for both the Unfiltered Approach and Couchbase-filtered selection - no external API key needed
- Static Alpine.js/Tailwind UI styled for Couchbase

Run from the repository root with:

```bash
docker compose up --build
```
