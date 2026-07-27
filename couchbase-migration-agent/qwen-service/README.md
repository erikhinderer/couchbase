# Qwen Service

Serves **Qwen 3 (8B)** via Ollama's `qwen3:8b` tag over an HTTP API compatible with
`/api/chat` and `/api/embeddings`, matching what `backend/app/core/qwen_agent.py`
expects.

> Note: an earlier version of this service used `qwen2.5:3.8b`, which is not a real
> Ollama model tag (Qwen2.5 only ships as 0.5b/1.5b/3b/7b/14b/32b/72b) and fails to
> pull with "pull model manifest: file does not exist". `qwen3:8b` is an actual
> published tag, and the closest match to "Qwen 3.8" read as "Qwen 3, 8B".

- Chat completions power the in-app migration assistant (`AgentPanel` in the UI).
- Embeddings power agent long-term memory recall (`backend/app/memory/couchbase_memory.py`)
  -- via native Couchbase Vector Search (Enterprise/Capella), with an in-process
  cosine-similarity fallback if the vector index is ever unavailable (see that
  module's docstring).

If you'd rather run a different local inference server (vLLM, llama.cpp server,
LM Studio, etc.), just point `QWEN_BASE_URL` in `.env` at it -- the backend only
needs the two Ollama-shaped endpoints above.

To swap in a different Qwen checkpoint, change `QWEN_MODEL` here and
`QWEN_MODEL_NAME` / `QWEN_EMBEDDING_MODEL_NAME` in `.env` to match -- and update
`MEMORY_EMBEDDING_DIMS` (backend/app/config.py) and `couchbase-memory/vector_index.json`
to match that model's actual embedding output length if you're on Enterprise/Capella.
