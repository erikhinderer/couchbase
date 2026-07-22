# Couchbase JSON Vectorizer Agent

A Dockerized AI agent that creates real-time vector embeddings for JSON documents
stored in Couchbase, and validates that Couchbase Vector Search is fully operational
against the results.

A setup wizard walks through source connection details (with one or more buckets),
where vectorized documents should be written, and which embedding model to use. Once
launched, the agent backfills every existing JSON document in the configured
bucket(s) that doesn't yet have an embedding, then keeps polling for newly-created
documents and embeds those too -- continuously, with no further action needed.

﻿﻿<img width="2940" height="1626" alt="couchbase-json-vectorizer-agent-demo" src="https://github.com/user-attachments/assets/f50f1e49-0779-4600-b379-3b066f0a981f" />

## Architecture

| Component | Tech | Purpose |
|---|---|---|
| `frontend/` | React + TypeScript + Vite, served by nginx | Dark-mode UI: setup wizard, live dashboard, operations feed, agent chat. Runs on **port 443 with SSL**. |
| `backend/` | FastAPI (Python) | REST + WebSocket API, validation, the vectorizer engine itself |
| `embedding-service/` | FastAPI + `sentence-transformers` | Loads the user-selected Hugging Face embedding model and runs inference |
| `qwen-service/` | Ollama serving Qwen 3.8 (Qwen 3, 8B) | Local LLM for the in-app assistant only -- explains validation results, recommends a model. It never generates the document embeddings themselves. |

### Why Vector Search validation is a hard requirement, not a fallback

Couchbase Vector Search (vector-typed FTS fields) requires **Couchbase Server
Enterprise Edition 7.6.0+, or Capella** -- Community Edition rejects vector-typed
fields outright. The agent's
entire purpose is standing up real, queryable Couchbase Vector Search against your
documents, so `core/validator.py` treats an unsupported cluster as a blocking error,
not something to gracefully degrade around. Validation also creates the actual FTS
vector index (sized to the chosen model's dimensions) and a partial GSI index used
to efficiently find not-yet-vectorized documents.

### Backfill + continuous vectorization, from one code path

`backend/app/core/vectorizer_engine.py` repeatedly queries each configured bucket for
documents missing the embedding field, embeds them in batches, and writes the vector
back. Newly created documents are, by definition, also missing the field, so the same
loop that backfills existing data continues to pick up new documents indefinitely --
there's no separate "new document" code path. See that module's docstring for why
this polls (backed by a partial GSI index) rather than using a raw DCP change stream:
the public Couchbase Python SDK doesn't expose a stable high-level DCP API for
application code; Couchbase Eventing Service functions are the documented mechanism
for true sub-second reaction to mutations, and this agent's poll interval (a few
seconds, configurable) is effectively real-time for this purpose while staying fully
within the supported SDK.

### Text extraction from arbitrary JSON documents

Buckets can hold any document shape, so `vectorizer_engine.extract_embeddable_text()`
flattens every string (and scalar) leaf value in a document into one text blob,
capped at a configurable character limit, rather than requiring per-collection field
configuration. This is a deliberately generic default -- if you need embeddings over
specific fields only, that's a natural extension point in the same function.

## Quick start

```bash
cp env.example .env
docker compose up --build
```

- UI: **https://localhost** (self-signed certificate baked in at build time --
  your browser will warn about this on first visit; that's expected for local
  evaluation. Swap in a real certificate via the commented-out volume mounts in
  `docker-compose.yml` for anything beyond that.)
- API: http://localhost:8000 (docs at `/docs`)
- Embedding service: http://localhost:8500
- Qwen / Ollama API: http://localhost:11434

First boot pulls the Qwen model (`qwen3:8b`) -- this can take a few minutes;
subsequent starts are fast (cached in the `ollama_data` volume). Embedding models are
downloaded lazily on first use of each one (cached in `embedding_model_cache`), not
all 10 up front.

## Using the agent

1. **New Agent** → name the agent, enter source cluster connection details, and the
   bucket name(s) holding your JSON documents (use **+ Add bucket** for more than
   one). Test the connection.
2. **Destination** → check "Create vector embeddings on the same server..." to write
   the embedding field back into the source buckets in place (the common case), or
   uncheck it to point at a different cluster/bucket(s) for the vectorized output.
3. **Embedding model** → pick from the 10 most-downloaded text embedding models on
   Hugging Face (ranked by download count on the `sentence-similarity` listing,
   verified live in July 2026 -- see `backend/app/core/embedding_models.py` for the
   full list, dimensions, and citations).
4. **Launch** → review, then launch. This validates connectivity, confirms Vector
   Search is operable, creates the vector index, and starts the vectorizer: existing
   documents are backfilled first, then the agent keeps watching for new ones.

The dashboard shows server connections, bucket count, total JSON documents,
documents vectorized, vectorization rate (docs/min), and embeddings currently in
progress, with a live **Agent Operations** feed streaming down the right side of
every page. Ask the agent panel (bottom, next to the feed) about validation results
or which model to pick.

## Troubleshooting

- **"Embedding model loaded" check fails during validation, or `/warmup` /
  `/embed` return 400**: this means `embedding-service` couldn't load the
  selected Hugging Face model into `sentence-transformers`. The validation
  message now includes the real underlying error (not just a generic "400 Bad
  Request") -- but the full Python traceback is only in that container's own
  logs:
  ```bash
  docker compose logs -f embedding-service
  ```
  The most common causes, in order of likelihood:
  1. **No network egress from the container to huggingface.co** on first use of
     a given model (models are downloaded lazily, not baked into the image).
     Check the host/Docker network's outbound access; `embedding-service` needs
     to reach `huggingface.co` and `cdn-lfs.huggingface.co`.
  2. **The `embedding_model_cache` volume isn't writable** by the container
     (rare, but possible on some Docker Desktop/volume driver setups). The
     service creates `/data/hf-cache` at both build time and startup, but if
     the mounted volume itself is read-only or owned by a different uid, model
     downloads will fail.
  3. **Out of disk space** -- BGE-M3 is ~2.3GB; smaller MiniLM models are
     under 100MB. `docker system df` shows volume usage.
  4. Transient network hiccups on a cold container are retried automatically
     (`embedding-service/app/main.py` retries a model load once after a few
     seconds) but a persistent failure means one of the above.

## Configuration notes

- **Multiple source buckets**: the wizard's **+ Add bucket** button adds entries to
  `bucket_names` on `ClusterConnectionConfig`; each is validated, indexed, and
  vectorized independently.
- **Cross-cluster destination**: when "same server" is unchecked, destination
  buckets are mapped to source buckets by list position; if the destination list is
  shorter, the same bucket name is assumed to also exist on the destination (this is
  checked during validation).
- **Swapping the assistant LLM**: point `QWEN_BASE_URL` at any Ollama-compatible
  server; the backend only calls `/api/chat`.
- **Adding embedding models**: add an entry to `EMBEDDING_MODELS` in
  `backend/app/core/embedding_models.py` with the correct Hugging Face repo id and
  real output dimensionality (embedding-service works with any
  `sentence-transformers`-compatible model, not just the curated 10).
- **Scaling beyond one API replica**: `JobStore` (backend/app/core/store.py)
  persists to a JSON file for simplicity, same pattern as the Migration Agent's
  `MigrationStore`. Swap it for a Couchbase collection or Postgres table if you need
  multiple backend replicas.
- **TLS certificate**: `frontend/Dockerfile` bakes in a self-signed cert so the
  stack works out of the box. Mount your own `cert.pem`/`key.pem` over
  `/etc/nginx/ssl/` (see the commented volumes in `docker-compose.yml`) for anything
  beyond local evaluation.

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Embedding service
cd embedding-service && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8500

# Frontend
cd frontend && npm install
npm run dev   # http://localhost:5173, talking to the backend directly (no nginx/SSL)
```

Frontend is TypeScript strict-mode (`npm run build` runs `tsc -b`).
