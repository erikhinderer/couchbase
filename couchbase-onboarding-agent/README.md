# Couchbase Onboarding Agent

A Dockerized AI agent for migrating **MongoDB**, **Amazon DynamoDB**, **Redis**, **Apache
Cassandra**, and **Microsoft Azure Cosmos DB** into **Couchbase Server (Enterprise Edition)**
or **Couchbase Capella**.

This project follows the design and architecture of the sibling
[`couchbase-migration-agent`](../couchbase-migration-agent) project (Couchbase-to-Capella
migrations) and reuses as many of its components as practical: the same FastAPI + React +
Couchbase-backed-agent-memory + local Qwen LLM stack, the same wizard-driven UX, the same
websocket-streamed live progress model, and the same bottleneck-detection/auto-throttle
pattern. The two places it necessarily diverges are called out below.

## Quick start

```bash
cp env.example .env
# edit .env: set MEMORY_CB_PASSWORD, and CAPELLA_API_TOKEN/CAPELLA_ORG_ID if you want
# automatic destination bucket provisioning on Capella.
./scripts/setup-corporate-ca.sh
docker compose up --build
```

- UI: http://localhost:5173
- API: http://localhost:8000 (docs at `/docs`)
- Couchbase EE admin console (agent memory): http://localhost:8091
- Qwen / Ollama API: http://localhost:11434

First boot pulls the Qwen model (`qwen3:8b` by default) and initializes the Couchbase
Enterprise Edition memory store -- this can take a few minutes; subsequent starts are fast
(cached in the `ollama_data` / `couchbase_memory_data` volumes).

## Architecture

| Component | Tech | Purpose |
|---|---|---|
| `frontend/` | React + TypeScript + Vite | Dark-mode UI: setup wizard, topology diagram, live stats dashboard, agent chat |
| `backend/` | FastAPI (Python) + five source SDKs + Couchbase SDK | REST + WebSocket API, validation, extract/transform/load pipeline, CDC |
| `qwen-service/` | Ollama serving Qwen 3, 8B | Local LLM for the in-app assistant and memory embeddings -- nothing leaves the Docker network |
| `couchbase-memory/` | Couchbase Enterprise Edition (free, dev/test license) | Agent long-term memory (past validations, decisions, bottleneck findings), recalled via native vector search |
| `scripts/init_memory.py` | Python | One-shot bootstrap: creates the memory bucket/scope/collection and the FTS vector index |

> **`couchbase-memory` is not a migration source or destination.** It's the onboarding
> agent's own memory store. Your actual source database (MongoDB/DynamoDB/Redis/
> Cassandra/Cosmos DB) and destination Couchbase cluster or Capella project are external
> systems, configured per-migration in the wizard -- nothing about them lives in this
> Docker Compose stack. See the sibling `couchbase-migration-agent`'s README for the
> Enterprise Free license terms that apply to the `couchbase:enterprise-*` image used here.

### Connector abstraction

Every source type implements a common `SourceConnector` interface
(`backend/app/core/connectors/base.py`):

- `test_connection()` -- introspects the source and returns a topology snapshot: server
  version/edition, per-container (collection/table/keyspace/container) estimated document
  count and size, sample field names, and whether continuous change-data-capture is
  available right now.
- `extract()` -- a full, batched read of the selected containers, transforming
  source-native types (BSON, DynamoDB's AttributeValue JSON, Cassandra's uuid/decimal/blob,
  ...) into JSON-safe Couchbase documents.
- `stream_changes()` -- yields ongoing change events for continuous replication, using each
  source's own native change-capture mechanism.

`backend/app/core/couchbase_loader.py` is the other half of the pipeline: it writes
`SourceDocument` batches into Couchbase via the Python SDK, one scope/collection per source
container, with `asyncio`-bounded concurrency that `MigrationEngine`'s bottleneck-detection
loop can throttle down live in response to destination backpressure or source rate-limiting
-- the direct analogue of the sibling project's cbbackupmgr `--threads` auto-throttle, just
applied to a worker pool this app owns outright instead of a subprocess it launches.

### Why there's no separate backup step

The sibling `couchbase-migration-agent` always takes a full `cbbackupmgr` backup of the
source *before* touching it, because its migration path (`cbbackupmgr backup` /
`restore` / XDCR) can and does modify the source's replication topology and is built around
a tool that can also restore the source from that same archive if something goes wrong.

**Every connector in this project is strictly read-only against the source.** Nothing here
ever writes to, deletes from, or reconfigures MongoDB, DynamoDB, Redis, Cassandra, or Cosmos
DB. That makes a pre-migration backup-and-restore-on-failure step redundant -- there's
nothing to protect the source *from*, and re-running extraction is always safe (Couchbase
upserts are naturally idempotent). Consequently:

- The wizard has one fewer step than the sibling project's (no "Backup" step between
  "Validate" and "Review & Approve").
- **Rollback** means undoing the *destination* side: stop any active change-data-capture and,
  if requested, delete every document this migration wrote to Couchbase (tagged via each
  document's `_migration.migration_id` field). The source is never touched, so there is
  nothing to restore there.

### Migration pipeline modes

```
validate -> await approval -> [replication mode] -> verify -> COMPLETE
                 (no backup phase -- see above)
```

| Mode | User-facing label | What happens | Terminal state |
|---|---|---|---|
| `full_load` | **One-time migration** | Every included container is extracted and loaded into Couchbase once | `COMPLETE` after transfer + verification |
| `cdc_live` | **Continuous replication** | Change-data-capture starts immediately and stays running | `REPLICATING` (ongoing) until stopped |
| `full_load_and_cdc` | **Bulk copy + continuous sync** | A full load for existing data, then change-data-capture takes over the ongoing delta | `REPLICATING` (ongoing) until stopped |

The "Ask the agent" recommendation on the Destination & Mode step
(`backend/app/core/recommendation.py`) is a fast, deterministic rule engine, not a live LLM
call -- same rationale as the sibling project: a wizard step on the critical path of setting
up a migration shouldn't be exposed to LLM latency or a hallucinated recommendation.

### Source -> Couchbase data modeling

| Source concept | Couchbase document key | Notes |
|---|---|---|
| MongoDB document (`_id`) | `collection::<_id>` | BSON types (ObjectId, Date, Decimal128, Binary) converted to JSON-safe values |
| DynamoDB item (partition[+sort] key) | `table::<pk>[::<sk>]` | Read via the table's real `KeySchema`, not guessed per item |
| Redis key | `redis::<key>` | Grouped into logical "containers" by the segment before the first `:` in the key name; value wrapped as `{redis_type, value, ttl}` |
| Cassandra row (partition+clustering key) | `table::<pk>[:<ck>]` | Row read via `dict_factory`; collection/counter columns preserved as JSON arrays/objects |
| Cosmos DB item (`id` + partition key) | `container::<pk>::<id>` | System properties (`_rid`, `_self`, `_etag`, `_attachments`) stripped; `_ts` kept as `_cosmos_ts` |

Every migrated document also gets a `_migration` envelope (`migration_id`, `source_container`,
`migrated_at`) used for verification counts and rollback purges.

### Connector implementation depth

**MongoDB is the reference-depth connector** -- the pattern the other four follow:

- Full introspection (server version, replica-set detection, per-collection `collStats`,
  sample fields).
- Full batched extraction via `find()`.
- Continuous sync via native MongoDB **Change Streams** (resumable, one watcher thread per
  collection, running concurrently).

The other four are **working, but intentionally lighter on edge-case hardening**:

- **Amazon DynamoDB** -- introspection via `describe_table`; extraction via paginated
  `Scan`; continuous sync via **DynamoDB Streams**. Does not follow shard splits/merges --
  a shard that closes mid-run stops producing events until the migration restarts (at which
  point the current shard set is picked up fresh). A production deployment against a
  high-throughput table would want the Kinesis Client Library-style shard-tree-following
  logic AWS's own DynamoDB Streams Kinesis Adapter provides.
- **Redis** -- keys grouped into logical containers by their `prefix:` naming convention;
  full extraction via `SCAN` with type-aware reads (`GET`/`HGETALL`/`LRANGE`/`SMEMBERS`/
  `ZRANGE`/`XRANGE`); continuous sync via **keyspace notifications** (pub/sub). This is
  Redis's only built-in change-notification mechanism short of speaking the replication
  protocol as a replica, and it is **not durable** -- pub/sub has no backlog, so any event
  published while this app isn't actively subscribed (restart, network blip) is lost, not
  merely delayed. There is no resumable checkpoint for this connector for that reason.
- **Apache Cassandra** -- introspection via driver metadata plus `system.size_estimates`
  (Cassandra's own approximate, per-node partition-count table); full extraction via a paged
  `SELECT *`; continuous sync is **polling-based**, not log-based: Cassandra's real CDC
  feature writes raw commit-log segments to a `cdc_raw` directory on each node's local
  filesystem, which requires an agent co-located with every node -- not something this
  centrally-running app has access to. Instead, this connector re-scans each table on an
  interval and uses `WRITETIME(<column>)` to find rows written since the last poll. Trade-
  offs: every poll re-scans the whole table (no server-side "changed since" filter exists),
  and **deletes are not detected at all** -- re-run a full load to reconcile deletes.
- **Microsoft Azure Cosmos DB** -- closest to reference depth. Its change feed is native,
  durable, and resumable via continuation tokens by default on every container, so
  continuous sync here doesn't carry Redis's or Cassandra's caveats. The one limitation is
  inherent to Cosmos DB itself: the default "Latest Version" change feed mode **does not
  surface deletes** (Cosmos does offer a newer "All Versions and Deletes" mode on some API
  versions, intentionally not used here to keep one code path working across accounts).

### Bottleneck detection & auto-throttle

`backend/app/core/bottleneck_detector.py` watches the extract/load pipeline for stalled or
degraded throughput, source rate-limiting errors, and Couchbase write backpressure (elevated
upsert failure rates). Because this pipeline is an `asyncio` worker pool this app owns
outright (not a subprocess like the sibling project's `cbbackupmgr`), **both** resource-
pressure bottlenecks *and* the sibling project's thread-count lever have a direct analogue
here: `CouchbaseLoader`'s concurrency can be reduced live, without restarting anything, in
response to `SOURCE_THROTTLED` or `DEST_BACKPRESSURE` findings. Stalled/degraded throughput
findings stay diagnosis-and-suggestion only in the Ask The Agent panel, same rationale as the
sibling project: a concurrency change doesn't fix a dead connection or a real network problem.

## Wizard flow

1. **Source** -- pick a source type, fill in its connection fields (only the relevant ones
   are shown), and click **Test & introspect source**. On success you'll see a `Connected ·
   N containers · <version>` badge with the detected containers, estimated counts, and
   whether continuous sync is currently available.
2. **Destination & Mode** -- Couchbase connection details (check **This endpoint is a
   Couchbase Capella cluster** to force TLS and reveal the optional project/cluster ID
   fields for bucket auto-provisioning), the destination bucket name, an **Ask the agent**
   card that recommends a replication mode from a cutover-vs-phased question, the
   replication mode selector itself (continuous modes are disabled if the source doesn't
   currently support change-data-capture), per-container include/exclude checkboxes, and a
   concurrency setting. **Create & validate** creates the migration record and immediately
   runs validation.
3. **Validate** -- source connectivity/edition, destination connectivity/capacity, CDC
   availability for the chosen mode, container-name-sanitization collisions, an average
   document-size sanity check against Couchbase's 20 MiB limit, network latency, and TLS
   configuration. Failed (red) checks block **Continue**; warnings (yellow) don't.
4. **Review & Approve** -- a summary card, an approver name field, and **Approve & view
   migration**, which takes you to the migration's detail page.
5. **Start** -- on the detail page, once the migration is `approved`, click **Start
   migration** (labeled **Start replication** for the two continuous modes). Live
   throughput/mutations-per-second/error-rate stream over the websocket onto the same
   topology diagram used throughout the wizard. One-time migrations run to `complete` on
   their own; continuous modes settle into `replicating` and stay there until you **Cutover
   & complete** or **Stop replication** from this same page.

## Configuration notes

- **Swapping the LLM**: point `QWEN_BASE_URL` at any Ollama-compatible server; the backend
  only calls `/api/chat` and `/api/embeddings`.
- **Scaling beyond one API replica**: `MigrationStore` (`backend/app/core/store.py`)
  persists to a JSON file for simplicity, same as the sibling project. Swap it for a
  Couchbase collection or Postgres table if you need multiple backend replicas.
- **Capella reachability**: Capella requires the backend container's egress IP to be
  allow-listed on the destination cluster (Capella project -> Allowed IPs) and connections
  over `couchbases://`.
- **Source reachability**: each source database needs to accept connections from this
  agent's egress IP -- security group / firewall / IP allow-list rules per source type, the
  same operational requirement the sibling project documents for Couchbase source clusters.
- **Container-name collisions**: source container names are sanitized into valid Couchbase
  scope/collection names (`sanitize_couchbase_name` in `backend/app/core/couchbase_client.py`);
  the validator's `NAMING_COMPAT` check flags any two source containers that would collide
  after sanitization, and the wizard lets you set an explicit target scope/collection name
  per container to resolve it.
- **Cassandra CDC poll interval / DynamoDB scan page size / etc.**: tunable via environment
  variables in `backend/app/config.py` (`CASSANDRA_CDC_POLL_INTERVAL_S`,
  `DYNAMODB_SCAN_PAGE_SIZE`, `REDIS_SCAN_COUNT`, `COSMOSDB_CHANGE_FEED_POLL_INTERVAL_S`, ...).

### Troubleshooting a build failure behind a corporate proxy

Same fix as the sibling project -- see its README's "Troubleshooting a build failure behind
a corporate proxy" section. In short:

```bash
./scripts/setup-corporate-ca.sh
docker compose build --no-cache
docker compose up
```

### Troubleshooting first boot

`couchbase-memory` can legitimately take a couple of minutes to come up on a cold boot. If a
*previous* `docker compose up` was interrupted partway through, it can be left in a
partially-initialized state that never becomes healthy on restart:

```bash
docker compose down -v   # wipes the named volumes
docker compose up --build
```

## Development

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install
npm run dev
```

Backend Python is standard `ast`/mypy-friendly style; frontend is TypeScript strict-mode
(`npm run build` runs `tsc -b && vite build`).

## Adding a sixth source

1. Implement `SourceConnector` in `backend/app/core/connectors/<name>.py` (see
   `mongodb.py` for the reference-depth pattern, or `redis_connector.py`/
   `cassandra_connector.py` for a lighter one built around a source with a non-obvious or
   non-durable change-capture story).
2. Register it in `backend/app/core/connectors/registry.py`.
3. Add the enum value to `SourceType` (`backend/app/models/enums.py`) and its connection
   fields to `SourceConnectionConfig` (`backend/app/models/schemas.py`).
4. Add its field list to `/api/source-types` (`backend/app/main.py`) and a form section to
   `SourceConfigForm.tsx` (`frontend/src/components/wizard/`).
5. Add the SDK to `backend/requirements.txt`.

No other backend code needs to change -- `MigrationEngine`, `MigrationValidator`,
`CouchbaseLoader`, and the API routes are all written against the `SourceConnector`
interface, not any specific source.
