# Qwen Service

Serves **Qwen 3 (8B)** via Ollama's `qwen3:8b` tag over an HTTP API compatible with
`/api/chat`, matching what `backend/app/core/qwen_agent.py` expects. This is the
"Qwen 3.8" the agent is required to run on, read as "Qwen 3, 8B" -- Qwen2.5 only
ever shipped as 0.5b/1.5b/3b/7b/14b/32b/72b, so a literal `qwen2.5:3.8b` tag
doesn't exist and fails to pull.

Chat completions power the in-app assistant panel: explaining validation
failures, recommending an embedding model, and summarizing job health. It plays
no part in generating the actual document vector embeddings -- those come from
`embedding-service`, running the user's chosen Hugging Face model.

If you'd rather run a different local inference server (vLLM, llama.cpp server,
LM Studio, etc.), point `QWEN_BASE_URL` in `.env` at it -- the backend only needs
the `/api/chat` endpoint above.
