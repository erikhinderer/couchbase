# Couchbase Migration Agent

A Dockerized AI agent for migrating Couchbase Server clusters — single-node, multi-cluster,
and Cross Data Center Replication (XDCR) topologies — to Couchbase Capella. Supports
Couchbase Server **7.2.0 through 8.0.2**.

<img width="1468" height="813" alt="image" src="https://github.com/user-attachments/assets/63eec8cd-64f7-4a9c-be75-3b2d53ea4411" />

## Quick start

```bash
cp env.example .env
# edit .env: set MEMORY_CB_PASSWORD, and CAPELLA_API_TOKEN / CAPELLA_ORG_ID if you want
# automatic destination bucket provisioning via the Capella Management API when not using a Couchbase Backup Full Admin or Administrator account.
./scripts/setup-corporate-ca.sh
docker compose up --build
```

- UI: http://localhost:5173
- API: http://localhost:8000 (docs at `/docs`)
- Couchbase EE admin console (agent memory): http://localhost:8091
- Qwen / Ollama API: http://localhost:11434

First boot pulls the Qwen model (`qwen3:8b` by default) and initializes the Couchbase
Enterprise Edition memory store — this can take a few minutes; subsequent starts are fast
(cached in the `ollama_data` / `couchbase_memory_data` volumes).

## Step-by-step wizard guide

This walks through the wizard exactly as it runs, from **New Migration** through the moment
data actually starts moving. Each step gates the next, and every "test"/"validate" action is
a live check against your real source and destination clusters, not a syntax check — a
green badge means the app actually talked to that cluster successfully.

*You will need an account on the source and destination servers with the required permissions and the IP address / range of the Couchbase Migration Agent must be in each servers Allowed IP Addresses. Link to reqiuired permissions: https://github.com/erikhinderer/couchbase/tree/main/couchbase-migration-agent#required-source-cluster-permissions

### 1. Source

- **Migration name** — a free-text label used everywhere else in the app (dashboard, the
  migration detail page's title, and anything the agent recalls from memory about this run).
- Fill in the source cluster's **Friendly name**, **Connection string**
  (`couchbase://host1,host2`, or `couchbases://...` if the source itself uses TLS),
  **Username**, **Password**, and whether to **Use TLS**.
- If the source is on a cloud VM or Kubernetes (EC2, GKE, CAO, etc.), check **Cluster is on a
  cloud VM or Kubernetes**. This makes `cbbackupmgr` resolve nodes via their external/alternate
  address instead of an internal one it can't reach from outside the cluster's network — but
  the checkbox alone isn't enough; alternate addressing also has to be configured on the
  cluster itself (see "Troubleshooting backups against cloud VMs / Kubernetes" below).
- Click **Test & introspect source**. On success you'll see a `Connected · N buckets ·
  vX.X.X` badge; on failure, the error banner explains why (bad credentials, unreachable
  host, TLS mismatch, etc.) rather than a generic failure.
- The source form never shows a "this is Capella" toggle — the source is always treated as a
  self-managed cluster.
- Nothing is created on the backend yet at this step; **Next** just advances the wizard.

#### Required source cluster permissions

The wizard shows a short reminder of this right below the Source password field. The
simplest option is a Couchbase user with the **Full Admin** role — guarantees both
`cbbackupmgr` and the app's own topology/validation/bottleneck-monitoring REST calls all
work without hitting a permission wall partway through a migration.

For least privilege instead, per Couchbase's own
[cbbackupmgr RBAC documentation](https://docs.couchbase.com/server/current/backup-restore/cbbackupmgr-backup.html#rbac):

- **Data Backup & Restore** (`data_backup`), scoped to the buckets being migrated — covers
  `cbbackupmgr`'s own backup/restore of KV data plus each bucket's view/GSI/FTS index
  *definitions*.
- **Read-Only Admin** (`ro_admin`), cluster-wide — this app's own introspection (bucket/node
  enumeration for validation, XDCR remote detection, and the CPU/memory polling behind
  bottleneck auto-throttling) reads cluster-admin REST endpoints that `data_backup` alone
  doesn't cover.
- Only if the source cluster actually uses them, since `data_backup` explicitly can't read
  this cluster-level (not per-bucket) data and `cbbackupmgr` will otherwise fail with a
  permissions error partway through backup: **Analytics Admin** (`analytics_admin`) for
  Analytics synonyms, **Eventing Full Admin** (`eventing_admin`) for Eventing functions, and
  **Search Admin** (`fts_admin`) for FTS aliases.

If you'd rather not grant one of the cluster-level roles above for a service you're not
using, `cbbackupmgr` supports disabling that service for the backup instead (this app doesn't
currently expose that as a wizard toggle) — see the `--disable-*` flags in
[`cbbackupmgr config`](https://docs.couchbase.com/server/current/backup-restore/cbbackupmgr-config.html).

#### Allow-listing the agent's IP address

Below the **Connection string** field, on both the Source step and the Destination & Mode
step, the wizard reminds you to allow-list this agent's IP range on that cluster before
testing the connection. If a cluster restricts inbound connections — a firewall, an
AWS/GCP/Azure security group, or Couchbase's own IP allow-listing (common on Capella, and
available on self-managed clusters too) — a request from an unlisted address typically hangs
until it times out, or is refused outright, rather than failing with a clear permissions
error. This can look identical to "the cluster is unreachable" or "wrong hostname," which
makes it easy to misdiagnose, and it applies equally to the destination (Capella) cluster,
not just the source.

What to allow-list depends on where this agent is running:

- **On the same machine/network as the source cluster** (e.g. both on the same EC2 VPC):
  usually nothing extra is needed, or you can allow-list the instance's private IP.
- **Running elsewhere and reaching the source over the public internet**: allow-list the
  public IP address of the machine running this application (the same address covered in
  the deployment `.env`/security-group setup, if you followed a guide for that). If you're
  not sure what address the source cluster will see, run `curl ifconfig.me` (or similar)
  from inside the `backend` container (`docker compose exec backend curl -s ifconfig.me`) to
  see its outbound IP as the source cluster would observe it.
- **Behind NAT, a VPN, or a corporate proxy**: the outbound IP the source cluster sees may
  not match any address you'd expect from looking at this machine directly — the `curl
  ifconfig.me` check above is the most reliable way to confirm it either way.

### 2. Destination & Mode

- Same connection fields as Source, plus a **This endpoint is a Couchbase Capella cluster**
  checkbox. Checking it forces `couchbases://` and TLS on (Capella requires both) and reveals
  two optional fields, **Capella project ID** and **Capella cluster ID** — set both (along
  with `CAPELLA_API_TOKEN`/`CAPELLA_ORG_ID` in `.env`) if you want the agent to
  auto-provision missing destination buckets via the Capella Management API; leave them
  blank if the destination buckets already exist.
- Click **Test destination connection** for the same kind of live check as the source step;
  success shows a `Reachable` badge.

#### Ask the agent: which replication mode fits?

Once the source has been introspected, an **Ask the agent** card appears above the
Replication mode choices, asking one question: are you planning to cut every application
over to the destination at the same time, or migrate them gradually (a phased cutover)?
Answering calls a recommendation engine (`POST /api/agent/recommend-replication-mode`) with
your answer plus the source topology already fetched in step 1 — XDCR remotes in use, bucket
count, and total/per-bucket data size — and returns a recommended mode with a plain-language
rationale, a rough one-time-transfer duration estimate, and any other considerations worth
knowing (e.g. a dominant bucket, or XDCR already pointed elsewhere). Click **Use this mode**
to apply it to the Replication mode selector below, or **Ask again** to reconsider.

This is deliberately a fast, deterministic rule engine (see
`backend/app/core/recommendation.py`) rather than a live call to the local Qwen LLM used for
chat — the rest of this app already leans toward explainable, non-LLM logic for anything that
gates a real infrastructure decision (see "Bottleneck detection" below), and a wizard step
shouldn't be exposed to LLM latency or a hallucinated recommendation. In broad strokes: a
phased cutover always points toward **Bulk copy + continuous sync** (data needs to stay in
sync while different applications switch over on their own schedules — a one-time snapshot
can't do that); an all-at-once cutover recommends **One-time migration** if the estimated
transfer comfortably fits a single maintenance window, or **Bulk copy + continuous sync**
if it wouldn't, since staging most of the data ahead of time shrinks the actual outage window
even when every application still switches over at once. The duration estimate is a rough
planning figure, not a measurement — real `cbbackupmgr` throughput depends on network, disk,
and cluster load that can't be known ahead of time.

- Choose a **Replication mode** — this is a one-time choice made here, not something you
  switch later without starting a new migration:
  - **One-time migration** — a single `cbbackupmgr restore` snapshot; the migration finishes
    once the transfer completes.
  - **Continuous replication** — XDCR streams changes indefinitely starting right after
    approval; you stop it later from the migration detail page (cutover or halt).
  - **Bulk copy + continuous sync** — a one-time restore for existing data, then XDCR takes
    over for the ongoing delta.

#### Bucket mapping

Below the Replication mode choices, every bucket detected on the source is listed with a
checkbox (migrate it or not — at least one must stay checked to proceed) and an optional
target bucket name field, showing each bucket's data size where available. Leaving the
target field blank restores into a same-named destination bucket, as before; filling it in
redirects that bucket's data to a differently-named destination bucket instead — useful for
consolidating buckets, or when the desired name is already taken on the destination.

Under the hood this uses `cbbackupmgr restore`'s `--map-data` flag (the same mechanism
already used to auto-resolve stale scope/collection id conflicts on repeated test
migrations — see "Troubleshooting backups" below) with a bucket-level mapping
(`source_bucket=target_bucket`) that redirects everything under that bucket unless a more
specific scope/collection rule overrides it. If a bucket has a mapping configured and
Capella auto-provisioning is enabled (project/cluster ID + `CAPELLA_API_TOKEN`/
`CAPELLA_ORG_ID` set), the *renamed* bucket is what actually gets created on the
destination — so a mapped bucket restores correctly even if the destination never had a
bucket under the source's original name.

- Clicking **Create & validate** is what actually creates the migration record on the
  backend (`POST /api/migrations`) and immediately runs validation — this is the point where
  a `migration_id` first exists, though nothing has touched your data yet.

### 3. Validate

- Shows the topology diagram (source cluster, destination cluster, and the migration agent
  in between) plus the full list of validation checks: version compatibility
  (7.2.0–8.0.2), connectivity, RBAC, schema/index compatibility, XDCR topology, TLS, and
  network latency.
- An XDCR remote satellite only appears on this diagram when the replication mode chosen in
  step 2 is one of the two continuous modes — a source cluster's own pre-existing, unrelated
  XDCR replications (e.g. left over from an earlier migration attempt against the same
  cluster) won't show up here for a plain one-time migration.
- Failed checks (red) block **Continue**; warnings (yellow) don't. Re-running validation
  isn't exposed as a standalone action in the wizard — go back and forward through the steps,
  or start a new migration, if you need to re-check after fixing something.

### 4. Backup

- Click **Run backup (cbbackupmgr)** to take a full backup of the source cluster — this
  always captures every bucket on the source, regardless of which buckets you plan to
  actually migrate (bucket selection is enforced later, at restore time).
- The backup runs on the server in the background; the wizard shows a live progress bar
  (percentage, items transferred, size, throughput, ETA) streamed over a websocket, so
  there's no need to keep a browser tab's request open or watch container logs to know it's
  still making progress.
- A failed backup shows its `cbbackupmgr` error inline (the card under the button expands
  with the error text) and offers **Retry backup**. **Continue** stays disabled until the
  backup's status is `complete` — a failed backup can't silently carry you into approval.
- The migration cannot be approved until this backup succeeds; it's also what rollback
  restores the source to if anything downstream goes wrong.
- If the agent detects the backup is overloading the source cluster's CPU, memory, or
  thread budget, it automatically restarts the backup with fewer threads and posts what it
  did in the Ask The Agent panel, which opens on its own — no action needed from you. Any
  bottleneck it can't fix by itself (e.g. a stalled or degraded transfer that isn't a
  thread-count problem) shows up there too, as a suggestion instead. See "Bottleneck
  detection" below.
- Once the backup completes, a **Download backup** button appears, marked *download
  optional — the migration itself doesn't need you to click it; rollback restores the source
  from the same server-side archive regardless. It's there for anyone who wants their own
  copy off this app's infrastructure (e.g. for archival, or to hand to another tool).
  Clicking it streams a zip of the backup archive directly from the backend
  (`GET /api/backup/{migration_id}/download`); for a large source cluster this zip can take
  a while to assemble server-side before the browser's download starts, since the archive is
  compressed on demand rather than pre-zipped.

### 5. Review & approve

- A summary card recaps the migration name, source, destination, replication mode, and
  backup status for one last check before anything is transferred.
- Clicking **Approve & View Migration to Start** approves the migration here — a named
  human sign-off that records who approved it and when — and takes you to the migration's
  detail page, where a separate **Start migration** button actually begins the transfer.

### 6. Start migration

- On the migration detail page, once the migration is in the `approved` phase, a **Start
  migration** button appears (labeled **Start replication** for the two continuous modes) —
  click it to actually begin moving data.
- From here, the same kind of live dashboard used throughout the wizard takes over:
  throughput, docs migrated, error rate, and ETA streamed over the websocket, rendered on the
  same topology diagram. One-time migrations run to `complete` on their own; continuous
  modes settle into `replicating` and stay there until you stop them (cutover or halt) from
  this same page.
- The agent keeps watching for bottlenecks during the restore too, but restore-phase
  findings are always a suggestion in the Ask The Agent panel rather than an automatic
  fix — see "Bottleneck detection" for why backup and restore are handled differently here.

## Architecture

| Component | Tech | Purpose |
|---|---|---|
| `frontend/` | React + TypeScript + Vite | Dark-mode UI: setup wizard, topology diagrams, live stats dashboard, agent chat |
| `backend/` | Couchbase SDK + FastAPI (Python) | REST + WebSocket API, validation, migration orchestration, backup/rollback |
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

### Migration Pipeline Modes

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

### Bottleneck detection

While a backup or restore is actively running, the agent watches for the bottleneck
patterns Couchbase's own support guidance calls out most often, and surfaces anything it
finds in the Ask The Agent panel — the panel opens on its own, so nothing needs to be
actively watched for this to work. Detection draws on two Couchbase sources:
["Troubleshooting Slow Couchbase Backup and Restore Processes"](https://support.couchbase.com/hc/en-us/articles/24941535204763)
for the causes it checks for, and the Backup Service's own thread-vs-CPU sizing formula
from ["Manage Backup Service Threads"](https://docs.couchbase.com/server/current/rest-api/backup-node-threads.html)
(`max(1, cpu_cores × 0.75)`) for one of the concrete thresholds.

What's checked, using data the app is already streaming or already has REST access to:

- **Stalled throughput** — transfer rate has been essentially zero for a sustained window,
  which usually points at a dropped connection rather than a merely slow one.
- **Degraded throughput** — rate has dropped well below this run's own peak for a sustained
  window, the pattern the support article associates with CPU/memory contention or network
  instability.
- **CPU saturation** — a relevant cluster node is running hot (~90%+) while the transfer is
  active.
- **Thread oversubscription** — the configured `--threads` value (the wizard's
  "parallelism" setting) is already above Couchbase's own recommended sizing for that
  node's CPU core count, even before CPU is fully saturated.
- **Memory pressure** — a relevant node is critically low on free memory during the
  transfer.

**During a backup, CPU saturation and memory pressure on the *source* cluster are handled
automatically** — both are real, currently-observed load, not just a configuration that
might become a problem, and the backup is a subprocess this app itself launched and fully
controls, so the agent stops that `cbbackupmgr` process and relaunches it with Couchbase's
own recommended thread count, against a fresh backup archive (`cbbackupmgr` has no
supported way to resume a *backup* that was killed mid-write, unlike restore). The wizard's
Backup step shows an "Auto-throttling" badge for the few seconds this takes, and the agent
panel posts a 🔧 message once it's done — "reduced threads from 8 to 3 and restarted the
backup" — rather than a suggestion, since it's reporting something it already did. This
only ever throttles *down*, never below 1 thread, and caps itself at 3 restarts per backup;
if the cluster is still saturated after that, throttling stops and the last detection
finding stands as a plain suggestion instead, same as everything else below.

**Thread oversubscription is deliberately excluded from auto-throttling**, even though the
lever (`--threads`) is the same one CPU saturation and memory pressure use. It fires purely
from the configured value exceeding Couchbase's own sizing formula — a preemptive,
config-based check, not an observation that the source is actually struggling — and in
testing it fired at CPU utilization as low as 11%. Auto-restarting a backup on that alone
would cost time (a restart means starting the archive over from 0%) without the source
being under any real pressure to relieve, which runs against the whole point of
auto-throttling. It still shows up as a suggestion in the agent panel — worth acting on for
future runs against that cluster — it just doesn't trigger an automatic restart.

Everything else stays diagnosis-and-suggestion only, for the agent to raise in chat rather
than act on: stalled/degraded throughput (on either phase) isn't a thread-count problem —
per the support article, it's usually a network or connectivity issue, so more or fewer
threads wouldn't address it — and restore-phase findings aren't auto-remediated at all,
since the destination side isn't a process this app can safely stop and relaunch the way it
can its own backup subprocess (restore already has its own retry loop for a different
failure mode — see `--map-data` under Troubleshooting backups — and mixing that with a
thread-count restart mid-flight was more risk than this app takes on automatically). Each
suggestion is specific and actionable (lower `--threads`/`--data-rate-limit` for the next
run, check network stability, etc.) rather than a generic "it's slow" message.

Node-level CPU/memory visibility depends on the cluster's management REST API being
reachable with sufficiently privileged credentials — reliably true for a self-managed
source cluster, but **Capella doesn't expose this level of node detail**. Auto-throttling
only applies to the source cluster's backup anyway, and source clusters in this app's
supported migration path are self-managed, so this isn't a practical gap for that feature;
throughput-trend findings (stalled/degraded) work against either side since they're derived
entirely from `cbbackupmgr`'s own output.

### Troubleshooting a build failure behind a corporate proxy

If `docker compose up --build` fails during `pip install` or `npm install`, or `qwen-service`
keeps restarting while trying to pull the model, with something like:

```
SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
self-signed certificate in certificate chain
```

or

```
Error: pull model manifest: Get "https://registry.ollama.ai/...": tls: failed to verify
certificate: x509: certificate signed by unknown authority
```

your laptop is behind an SSL-inspecting corporate proxy (Zscaler, Netskope, Palo Alto
GlobalProtect, etc., usually pushed via MDM). Docker Desktop's own image pulls go through
macOS's system trust store, so those succeed — but pip/npm at build time, and Ollama's Go
binary when it pulls the model at container startup, each verify against their own bundled CA
stores, which have never heard of your proxy's root cert.

Fix:

```bash
./scripts/setup-corporate-ca.sh   # exports the cert from your Mac's keychain
docker compose build --no-cache
docker compose up
```

This drops the exported cert into `certs/`, `backend/certs/`, `frontend/certs/`, and
`qwen-service/certs/` (one per Docker build context) — gitignored, machine-specific, and a
no-op on machines that don't need it. macOS only; on Linux, save your org's proxy root CA as
`corporate-ca.crt` in each of those four directories yourself.

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
4. **A single request was held open for a long time and something in between reset it** (seen
   with the Backup step against a real cluster, where the request used to block for the whole
   backup duration). If this happens on an action that finishes almost immediately, it's one of
   the two causes above; if it happens on a long-running action, check whether the underlying
   operation actually completed anyway (the live progress bar / websocket-driven status is the
   source of truth, not the HTTP response) before assuming something is broken.

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
   (optionally) project/cluster IDs + an API token for automatic bucket provisioning. Answer
   the agent's cutover-vs-phased question for a recommended replication mode, and optionally
   redirect any source bucket to a differently-named destination bucket (see "Bucket
   mapping" above).
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
above). It also opens on its own during an active backup or restore if it detects a
bottleneck (stalled/degraded throughput, CPU/memory pressure, thread oversubscription). For
a backup that's overloading the source cluster, it doesn't just suggest — it automatically
restarts the backup with fewer threads and tells you here once it has; everything it can't
safely fix itself (restore-side bottlenecks, stalled/degraded transfers) still comes through
as a concrete suggestion instead. See "Bottleneck detection" under Architecture for the full
breakdown of what's automatic and what isn't.

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
