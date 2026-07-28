# Couchbase Migration Agent

A Dockerized AI agent for migrating Couchbase Server clusters — single-node, multi-cluster,
and Cross Data Center Replication (XDCR) topologies — to Couchbase Capella. Supports
Couchbase Server **7.2.0 through 8.0.2**.

Dark-mode React UI styled after familiar cloud data migration services, with Couchbase branding: cluster
topology diagrams, live migration/performance stats, validation checklists, backup/rollback
controls, and a user-approval gate before any data leaves the source cluster.

<img width="1470" height="884" alt="image" src="https://github.com/user-attachments/assets/f265d3d5-25d6-40d9-9f43-a96588b53d6e" />

## Architecture

| Component | Tech | Purpose |
|---|---|---|
| `frontend/` | React + TypeScript + Vite | Dark-mode UI: setup wizard, topology diagrams, live stats dashboard, agent chat |
| `backend/` | FastAPI (Python) | REST + WebSocket API, validation, migration orchestration, backup/rollback |
| `qwen-service/` | Ollama serving Qwen 3.8 | Local LLM for the in-app migration assistant and memory embeddings — nothing leaves the Docker network |
| `couchbase-memory/` | Couchbase Enterprise Edition (free, dev/test license) | Agent long-term memory (past validations, decisions, incidents), recalled via native vector search |
| `scripts/init_memory.py` | Python | One-shot bootstrap: creates the memory bucket/scope/collection and the FTS vector index |

> **Enterprise Edition, free for dev/test:** `couchbase-memory` runs `couchbase:enterprise-*`
> so native ANN vector search (FTS vector index) is available for agent-memory semantic
> recall. Couchbase's Enterprise Free license lets you download and run Enterprise Edition
> at no cost for internal development, testing, and evaluation; it only converts to a paid
> subscription if you use it in production or request Couchbase support — see
> https://www.couchbase.com/legal/agreements/ for the exact terms. If that doesn't fit your
> use case, point `MEMORY_CB_*` at Couchbase Capella instead (also has native vector search,
> fully managed, no code changes needed). `AgentMemoryStore.recall()`
> (`backend/app/memory/couchbase_memory.py`) falls back to a N1QL query + in-process
> cosine-similarity scan if the vector index is ever momentarily unavailable, purely as a
> resilience net — it's not the expected steady-state path.

### Migration pipeline

Every migration always starts the same way — validate the source/destination, back up
the source, and require human approval — regardless of how data is transferred:

```
validate → backup (cbbackupmgr, source) → await user approval → [replication mode] → …
                 ↳ rollback available at any gated step (restores source from backup)
```

At the wizard's **Destination & Mode** step, the user picks one of three replication
modes (`MigrationStrategy`):

| Mode | User-facing label | What happens | Terminal state |
|---|---|---|---|
| `backup_restore` | **One-time migration** | `cbbackupmgr restore` targets the destination once | `COMPLETE` after transfer + verification |
| `xdcr_live` | **Continuous replication** | XDCR is configured from source → destination and left running indefinitely, starting right after approval | `REPLICATING` (ongoing) until the user stops it |
| `hybrid` | **Bulk copy + continuous sync** | One-time `cbbackupmgr restore` for existing data, then XDCR takes over for the ongoing delta | `REPLICATING` (ongoing) until the user stops it |

For the two continuous modes, the migration detail page exposes two controls once
replication is live:
- **Cutover & complete** — stops XDCR, verifies the destination, and marks the migration
  `COMPLETE` (destination becomes the system of record).
- **Stop replication** — stops XDCR without cutover; the migration ends in `STOPPED` and
  the source remains authoritative.

Live replication stats (mutations/sec, changes left, estimated time to catch up) are
polled from Couchbase's XDCR task API every few seconds and streamed to the dashboard
over the same websocket used for one-time transfer progress.

One-time transfers use `cbbackupmgr backup` against the source, then `cbbackupmgr
restore` targeting the destination (Capella or on-prem) — Couchbase's documented path
for cluster migration. XDCR-replicated topologies already present on the source are also
detected automatically during validation and surfaced in the topology diagram, regardless
of which replication mode is chosen for the migration itself.

## Quick start

```bash
cp env.example .env
# edit .env: set MEMORY_CB_PASSWORD, and CAPELLA_API_TOKEN / CAPELLA_ORG_ID if you want
# automatic destination bucket provisioning via the Capella Management API.

docker compose up --build
```

- UI: http://localhost:5173
- API: http://localhost:8000 (docs at `/docs`)
- Couchbase EE admin console (agent memory): http://localhost:8091
- Qwen / Ollama API: http://localhost:11434

First boot pulls the Qwen model (`qwen3:8b` by default) and initializes the Couchbase
Enterprise Edition memory store — this can take a few minutes; subsequent starts are fast
(cached in the `ollama_data` / `couchbase_memory_data` volumes).

### Troubleshooting first boot

`couchbase-memory` (Couchbase Server itself) can legitimately take a couple of minutes to
come up on a cold or resource-constrained boot — its healthcheck is tuned generously to
avoid a false "unhealthy" that blocks `memory-init`/`backend` from starting. If a *previous*
`docker compose up` was interrupted partway through (e.g. another service failed and Compose
tore the stack down mid-init), `couchbase-memory` can be left with partially-initialized
state in its volume that never becomes healthy on restart, no matter how long you wait. If
you hit that:

```bash
docker compose down -v   # wipes the named volumes (couchbase_memory_data, ollama_data, etc.)
docker compose up --build
```

That's a clean-slate reset — you'll re-pull the Qwen model and re-run `memory-init` from
scratch, but it avoids chasing a wedged container.

**Containers reliably starting on their own.** The project pins an explicit Compose
`name:` and no longer sets fixed `container_name:` values per service, and every long-running
service has `restart: unless-stopped`. This matters for two concrete failure modes we hit
during development:

- *"Container name '/cma-qwen' is already in use"* — happened when a container from an
  earlier, differently-scoped `docker compose` invocation (e.g. the project folder got
  renamed or re-extracted between runs) was still around under a name this project also
  wanted to use. Pinning the project name and dropping fixed container names means Compose
  scopes container identity consistently and won't collide like this.
- *`backend`/`frontend` not starting even though nothing looked wrong* — `backend` used to
  hard-depend on `memory-init` exiting `0`. `memory-init` is a best-effort setup step (create
  the agent-memory bucket/scope/collection/index); the backend's own memory code already
  tolerates that not having happened yet, so it no longer blocks on it. `memory-init` also
  now retries itself (`restart: on-failure:5`) instead of requiring a manual re-run if it
  loses a startup race with Couchbase.

If you still see a stale-container conflict after pulling this update, `docker compose down`
(from *this* project folder) will clean up containers under the pinned project name; add `-v`
per the section above if you also want a clean data volume.

### Troubleshooting backups

The **Backup** wizard step shows the actual `cbbackupmgr` error inline when a backup fails
(the "Run backup" card expands with the error text) -- check that first. The same detail is
also always in the backend logs: `docker compose logs backend --tail 100`. The most common
causes, roughly in order of likelihood:

- **Source hostname isn't reachable from inside the `backend` container.** If your source
  Couchbase Server is running on the same machine you're running Docker on (e.g. a local dev
  cluster), `localhost` or `127.0.0.1` in the source connection string refers to the `backend`
  container itself, not your host machine -- cbbackupmgr will fail to connect. On Docker
  Desktop (macOS/Windows), use `host.docker.internal` instead of `localhost`/`127.0.0.1` in the
  source connection string. On Linux, either use the host's LAN IP or add
  `extra_hosts: ["host.docker.internal:host-gateway"]` to the `backend` service in
  `docker-compose.yml`.
- **Wrong username/password** for the source cluster, or a user without backup/read
  permissions on the buckets being migrated.
- **TLS mismatch** -- `couchbases://` was specified but the source isn't actually configured
  for TLS (or vice versa), or a self-signed cert without `ca_cert_path` set.
- **`/data/backups` (the `backup_data` volume) is out of space** -- check
  `docker system df -v` / `docker exec couchbase-migration-agent-backend-1 df -h /data/backups`.
- **`cbbackupmgr` binary missing** -- surfaces as a different, more obvious error at container
  startup (see the `cbbackupmgr not found` note under Configuration notes below), not a backup
  failure with an archive path.

### Troubleshooting "NetworkError" / "Failed to fetch" in the wizard

If a red banner reading something like "NetworkError when attempting to fetch resource."
(Firefox) or "Failed to fetch" (Chrome) appears the moment you click a button in the wizard
(e.g. "Test & introspect source"), that's the *browser* failing to reach the migration
agent's own backend API -- it happens before the backend ever talks to your source/destination
Couchbase cluster, so it's unrelated to those clusters' reachability. In order of likelihood:

1. **The `backend` container isn't actually up.** Run `docker compose ps` -- if `backend`
   shows `Restarting` instead of `Up`/`healthy`, check `docker compose logs backend --tail 50`
   for the reason (and make sure you're on the latest version of this project; several
   earlier startup bugs are fixed in the version you're reading this in).
2. **You're loading the UI from somewhere other than `http://localhost:5173`** (a LAN IP, a
   different hostname, a remote box) but the backend's `CORS_ORIGINS` and the frontend's
   `VITE_API_BASE_URL`/`VITE_WS_BASE_URL` are still set to their `localhost` defaults. Set all
   three in `.env` to match how you actually access the UI, then rebuild --
   `VITE_API_BASE_URL`/`VITE_WS_BASE_URL` are baked into the frontend's JS at *build* time by
   Vite, so `docker compose up --build` (not a plain `up`, and not a container restart) is
   required for a change to take effect. See the `.env` comments for the exact variables.
3. Once on a version with this troubleshooting section, the error message itself will name the
   exact URL the browser tried to reach -- that's usually enough to tell which of the above it is.

### Troubleshooting backups against cloud VMs / Kubernetes ("connection refused" partway through)

If a backup connects fine, transfers several kinds of metadata, then fails with
`Error backing up cluster: connection refused` while "Transferring GSI index definitions" (or
similar) -- this is specific to clusters on AWS/GCP/Azure VMs or Kubernetes (CAO). It happens
because Couchbase nodes normally advertise their *internal* network address (private IP /
internal DNS name) in the cluster map; `cbbackupmgr`'s initial connection uses the hostname you
typed, but once it needs to reach a specific node/service directly -- notably the Index
Service, for GSI index definitions -- it falls back to that internal address, which isn't
reachable from outside the cluster's network (your laptop, in this setup).

Fix, in order:

1. **Check "Cluster is on a cloud VM or Kubernetes"** in the wizard's Source/Destination step
   for the affected cluster. This appends `?network=external` to the `-c/--cluster` argument
   cbbackupmgr uses, telling it to resolve each node via its alternate/external address instead
   of its internal one.
2. **That address has to actually be configured on the cluster**, or step 1 has nothing to
   resolve to. Set it via Couchbase's REST API (`Administrator`/cluster admin credentials
   required):
   ```
   curl -X PUT -u <username>:<password> \
     http://<your-cluster-host>:8091/node/controller/setupAlternateAddresses/external \
     -d hostname=<your-cluster-host> \
     -d kv=11210 -d n1ql=8093 -d fts=8094 -d cbas=8095 -d eventing=8096 -d backup=8097 \
     -d index=9102
   ```
   Use the same public hostname and the same port numbers you're already connecting on for
   every service except `index` -- the Index Service isn't part of the 8091-8097 range and
   needs its own port opened up (see next step). Verify with
   `curl -u <username>:<password> http://<your-cluster-host>:8091/pools/default/nodeServices`.
3. **Open the Index Service port in your firewall/security group**: port `9102` (Couchbase
   calls this `indexHttp`) in addition to whatever's already open. On AWS, add an inbound rule
   for TCP 9102 to the security group, same as was done for 8091-8097/11210. If you still hit
   connection issues after this, Couchbase's [full ports reference](https://docs.couchbase.com/server/current/install/install-ports.html)
   lists a few more Index Service ports (9100-9105) that may also need to be open depending on
   cluster topology.

This is inherent to how Couchbase's cluster map works, not specific to this app -- the same
setup is needed for any tool (including Couchbase's own CLI tools) connecting to a cloud-hosted
cluster from outside its network. See Couchbase's
[Managing Alternate Addresses](https://docs.couchbase.com/server/current/rest-api/rest-set-up-alternate-address.html)
docs for the full reference.

## Using the agent

1. **New Migration** → enter source cluster connection details, test/introspect it.
2. Enter destination (Capella) connection details — a Capella endpoint, DB credentials, and
   (optionally) project/cluster IDs + an API token for automatic bucket provisioning.
3. **Validate** — checks version compatibility (7.2.0–8.0.2), connectivity, RBAC, schema/index
   compatibility, XDCR topology, TLS, and network latency. Errors block progress; warnings don't.
4. **Backup** — runs `cbbackupmgr` against the source. The migration cannot be approved until
   this completes successfully.
5. **Approve** — a named human sign-off gate. Nothing transfers before this.
6. **Start** — live transfer begins; the dashboard streams throughput, docs migrated, error
   rate, and ETA over a websocket, rendered on an AWS-DMS-style topology diagram.
7. **Rollback** — available from the migration detail page at any point after a backup exists;
   restores the source cluster to its exact pre-migration state.

Ask the agent panel (bottom-right) about validation failures, migration strategy, or "what
happened last time we hit an XDCR warning like this" — it recalls similar past events from
Couchbase-backed agent memory via native vector search (see the Enterprise Edition note
above).

## Configuration notes

- **cbbackupmgr / cbrestore**: the backend Dockerfile pulls these straight out of the official
  `couchbase:enterprise-<version>` Docker Hub image (the same one `docker-compose.yml` uses for
  the agent memory store) via a multi-stage build, rather than installing a package from
  Couchbase's apt repo — that repo is gated for lead tracking and returns 403s to non-browser
  requests unpredictably, which made the build flaky. Only the CLI binaries are copied out; the
  backend build stage never runs Couchbase Server itself. Change the `COUCHBASE_TOOLS_VERSION`
  build arg in `backend/Dockerfile` to pin a different tools version; it must be able to talk to
  servers in the 7.2.0–8.0.2 range. If `docker compose up --build` ever reports `cbbackupmgr not
  found` at container startup, rebuild with `--no-cache` — a multi-arch pull for that Couchbase
  image tag/platform may have failed silently.
- **Capella reachability**: Capella requires the backend container's egress IP to be allow-listed
  on the destination cluster (Capella project → Allowed IPs) and connections over `couchbases://`.
- **Swapping the LLM**: point `QWEN_BASE_URL` at any Ollama-compatible server; the backend only
  calls `/api/chat` and `/api/embeddings`.
- **Scaling beyond one API replica**: `MigrationStore` (backend/app/core/store.py) persists to a
  JSON file for simplicity. Swap it for a Couchbase collection or Postgres table if you need
  multiple backend replicas.

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install
npm run dev
```

Backend Python files are type-checked with standard `ast`/mypy-friendly style; frontend is
TypeScript strict-mode (`npm run build` runs `tsc -b`).
