# Couchbase MCP Tool Filtering Demo

A branded, Dockerized demo showing how Couchbase vector search can pre-filter MCP tools before a local Ollama LLM performs tool selection, while the unfiltered baseline sends every tool to the same local model. The demo is styled for Couchbase and uses insurance operations prompts around claims, policy servicing, underwriting, incidents, and agent support. All LLM inference runs locally on Ollama - no external API key or account is required.

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

- `couchbase` (Couchbase Server, **Enterprise Edition**) as the vector store and semantic cache, provisioned automatically by the `couchbase-init` service. Enterprise Edition is required here, not a preference - see "Why Enterprise Edition" below.
- `ollama/ollama` as the local LLM runtime for both approaches
- the FastAPI demo app on port `8089`

### Why Enterprise Edition

This demo's core feature - Couchbase vector search pre-filtering MCP tools - depends on FTS vector-typed index fields, which **Couchbase Community Edition does not support at all** (confirmed against 7.6.2 and still true as of CE 8.0: attempting to create a vector index returns `"vector typed fields not supported in this cluster"`). Running this demo against Community Edition doesn't degrade gracefully - it silently returns 0 tools from every vector search, which used to crash the request with an unrelated division-by-zero error and get masked by the UI as a fake $0.00 "success."

Couchbase Enterprise Edition's Docker image (`couchbase:enterprise-7.6.2`) is **free to run for development and testing** (not production) under Couchbase's standard license - no key or account needed for this use case. If you're upgrading an existing `couchbase-data` volume from an older copy of this project that used Community Edition, no action is needed - Enterprise Edition reads the same on-disk data, and since the vector indexes never successfully got created under Community Edition, they'll be created correctly on next startup. If anything looks off, `docker compose down -v && docker compose up --build` gives you a fully clean start.

By default, the compose workflow pulls `llama3.1:8b`. To use another Ollama model:

```bash
OLLAMA_MODEL=llama3.2:3b docker compose up --build
```

## Notes

- First startup can take longer because Docker pulls the Ollama model, initializes the Couchbase cluster/bucket/indexes, and Python downloads the SentenceTransformers embedding model. Couchbase Server's first boot inside Docker Desktop on Mac (virtualized disk I/O) commonly takes several minutes while it starts data, index, query, and FTS services one at a time - this is normal, not a hang.
- The app uses Ollama's OpenAI-compatible API endpoint at `/v1` as the client protocol only - all inference happens locally, nothing is sent to OpenAI.
- No API key is required to run the demo. If Ollama isn't reachable yet when the app starts, use the gear icon > **Reconnect to Ollama** once it's ready.
- The Couchbase Server Web Console is available at `http://localhost:8091` (default login `Administrator` / `CouchbaseDemo123!`, or whatever you set via `COUCHBASE_USERNAME` / `COUCHBASE_PASSWORD`).
- The app image installs the CPU-only build of `torch` before the rest of `requirements.txt`, so `pip` doesn't pull the default CUDA/cuDNN/Triton-enabled build (multiple GB of NVIDIA packages meant for GPU inference) - this container only runs a small embedding model on CPU; the actual LLM runs in the separate Ollama container.
- The `app` container's entrypoint (`wait-for-deps.sh`) blocks until Couchbase, the `mcp_demo` bucket, and the Ollama model are all actually ready before starting the app - this holds regardless of how the container was started (`docker compose up`, Docker Desktop's start/stop toggle, a plain `docker start`, etc.), and every service has `restart: unless-stopped` (or `on-failure` for the one-shot init containers), so the stack self-heals from most transient failures without manual re-running.

## Metrics

Both panels run the same local Ollama model, which has no real per-token billing. To still make the token savings from Couchbase filtering comparable in dollar terms, LLM Cost is calculated using reference hosted-API rates: **$5.00 per 1M input tokens** and **$30.00 per 1M output tokens** (overridable via `LLM_INPUT_COST_PER_1M` / `LLM_OUTPUT_COST_PER_1M`). A semantic cache hit skips the LLM call entirely, so it always shows $0.00. Token counts use real provider usage data when Ollama returns it, falling back to a `tiktoken` estimate (`cl100k_base`) otherwise.

Because the unfiltered panel sends every MCP tool definition to the LLM, the browser request timeout is set to 120 seconds to avoid returning zero metrics while the local model is still working through the larger prompt.

## Troubleshooting

**`couchbase-mcp-couchbase is unhealthy` / `dependency failed to start`**

This means the Couchbase Server container itself never answered its healthcheck (`http://localhost:8091/pools`) within the startup window. Before troubleshooting further, note that Couchbase Server's first boot inside Docker Desktop on Mac (virtualized disk I/O) commonly takes several minutes while it starts data, index, query, and FTS services one at a time - `docker compose ps` showing `health: starting` for several minutes on first run is expected, not necessarily a problem.

If it's still unhealthy well after that: this Couchbase Server version returns `401 Unauthorized` on the unauthenticated `/pools` endpoint instead of `200`. The healthcheck and `wait-for-deps.sh` intentionally do **not** use `curl -f` against that endpoint anymore, because `-f` treats any non-2xx response (including a 401 from an otherwise perfectly healthy server) as a permanent failure - which meant the check could fail forever regardless of whether Couchbase was actually up. If you're running an older copy of this project, that's almost certainly why `couchbase` (and anything waiting on it) never went healthy; re-download this version, or drop `-f` from any `curl .../pools` healthcheck yourself.

If it's still not coming up after that:

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
   docker exec couchbase-mcp-couchbase tail -60 /opt/couchbase/var/lib/couchbase/logs/info.log
   ```

**`app` (or `couchbase-init` / `ollama-pull`) never starts, even though `couchbase` and `ollama` are running**

Earlier versions of this compose file gated `couchbase-init`, `ollama-pull`, and `app` behind `depends_on: condition: service_healthy` / `service_completed_successfully`. In practice this condition-based wait could fail to trigger at all - the dependent container would sit stuck in `Created` indefinitely - especially after Docker Desktop's start/stop toggle, an interrupted run, or even some plain `docker compose up` sessions.

The compose file no longer relies on that mechanism. `depends_on` is now a plain (unconditioned) list, which only affects start order - every container is actually started by Compose immediately, with no health/completion gate to get stuck on. Each container that has a real dependency waits for it internally instead:

- `couchbase-init` polls `couchbase:8091` itself before provisioning (`init.sh`)
- `ollama-pull` polls `ollama list` itself before pulling the model
- `app`'s entrypoint (`wait-for-deps.sh`) blocks until Couchbase, the `mcp_demo` bucket, and the Ollama model are all genuinely ready before starting the server

So every container starts right away and becomes useful once its own dependency is actually ready, regardless of how the stack was launched. If a container still doesn't start, that's no longer a `depends_on` issue - check `docker compose logs <service-name>` for what it's actually doing.
