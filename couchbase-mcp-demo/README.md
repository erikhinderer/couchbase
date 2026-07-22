# Couchbase MCP Tool Filtering Demo

A demo showcasing how Couchbase vector search can pre-filter MCP tools before a local LLM performs tool selection, while the unfiltered baseline sends every tool to the same local model. The demo is styled for Couchbase and uses insurance operations prompts around claims, policy servicing, underwriting, incidents, and agent support. All LLM inference runs locally on Ollama - no external API key or account is required.

## What it demonstrates

The UI compares two approaches, both backed by the same local Ollama model:

1. **Baseline:** send the full MCP tool catalog to the LLM.
2. **Couchbase-filtered:** use Couchbase vector search (Search Service) and semantic caching to send only the most relevant tools to the LLM.

This highlights reductions in prompt tokens, latency, and context noise while preserving accurate tool selection - isolating the effect of vector-prefiltering rather than comparing different model providers.

## Run with Docker Compose

```bash
docker compose up --build
```

Then open:

```text
http://localhost:8089
```

### Startup status

The stack has five containers that need to come up in order (Couchbase Server → Couchbase provisioning, Ollama runtime → model pull, then the app), which makes the raw `docker compose up` log output hard to follow. For a clearer view of what's still starting vs. what's ready, use the included helper script instead:

```bash
./start.sh
```

This starts everything in the background and shows a live table like:

```text
Couchbase MCP Demo - startup status
====================================
  [ OK ] Couchbase Server         healthy
  [ OK ] Couchbase provisioning   done
  [ OK ] Ollama runtime           healthy
  [....] Ollama model pull        running
  [ .. ] Demo app                 pending

All services are up. Open http://localhost:8089
```

updating every few seconds until everything is ready (or reporting which service failed, with a pointer to `docker compose logs <service-name>`). Ctrl-C stops watching without stopping the containers - run `./start.sh` again any time to check status, or `docker compose down` to stop everything.

The compose file starts:

- `couchbase` (Couchbase Server, Community Edition) as the vector store and semantic cache, provisioned automatically by the `couchbase-init` service
- `ollama/ollama` as the local LLM runtime for both approaches
- the FastAPI demo app on port `8089`

By default, the compose workflow pulls `llama3.1:8b`. To use another Ollama model:

```bash
OLLAMA_MODEL=llama3.2:3b docker compose up --build
```

## Notes

- First startup can take longer because Docker pulls the Ollama model, initializes the Couchbase cluster/bucket/indexes, and Python downloads the SentenceTransformers embedding model.
- The app uses Ollama's OpenAI-compatible API endpoint at `/v1` as the client protocol only - all inference happens locally, nothing is sent to OpenAI.
- No API key is required to run the demo. If Ollama isn't reachable yet when the app starts, use the gear icon > **Reconnect to Ollama** once it's ready.
- The Couchbase Server Web Console is available at `http://localhost:8091` (default login `Administrator` / `CouchbaseDemo123!`, or whatever you set via `COUCHBASE_USERNAME` / `COUCHBASE_PASSWORD`).

## Metrics

Both panels run the same local Ollama model, which has no real per-token billing. To still make the token savings from Couchbase filtering comparable in dollar terms, LLM Cost is calculated using reference hosted-API rates: **$5.00 per 1M input tokens** and **$30.00 per 1M output tokens** (overridable via `LLM_INPUT_COST_PER_1M` / `LLM_OUTPUT_COST_PER_1M`). A semantic cache hit skips the LLM call entirely, so it always shows $0.00. Token counts use real provider usage data when Ollama returns it, falling back to a `tiktoken` estimate (`cl100k_base`) otherwise.

Because the unfiltered panel sends every MCP tool definition to the LLM, the browser request timeout is set to 120 seconds to avoid returning zero metrics while the local model is still working through the larger prompt.

## Troubleshooting

**`couchbase-mcp-couchbase is unhealthy` / `dependency failed to start`**

This means the Couchbase Server container itself never answered its healthcheck (`http://localhost:8091/pools`) within the startup window. It's almost always one of:

1. **Not enough memory allocated to Docker.** Couchbase Server needs headroom beyond the ~1GB of service RAM this demo requests (Docker Desktop → Settings → Resources). 6-8GB total for Docker Desktop is a safe minimum for this stack (Couchbase + Ollama + the app).
2. **A stale data volume from an earlier run.** If a previous `docker compose up` was interrupted or failed partway through cluster initialization, the `couchbase-data` volume can be left in a state where Couchbase Server never comes back up cleanly. Fix:
   ```bash
   docker compose down -v
   docker compose up --build
   ```
   (`-v` removes the named volumes - `couchbase-data`, `ollama-data`, `hf-cache` - so everything reprovisions from scratch.)
3. **Check the actual server logs** if the above doesn't resolve it:
   ```bash
   docker compose logs couchbase
   ```
