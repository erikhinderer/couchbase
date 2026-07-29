# Couchbase Data Generator

A small web app that generates mock Customer 360 profile documents and
writes them into a Couchbase Server (or Capella) bucket using the
**official Couchbase Node.js SDK** (`couchbase` npm package). It includes
a configuration wizard and a live dashboard with an animated data-flow
diagram, styled after the Couchbase Migration Agent UI.

## What it does

- Wizard collects: connection string, username, password, TLS toggle,
  bucket (+ optional scope/collection), and generation rate in MB/s
  (default **1 MB/s**).
- "Test & introspect cluster" validates credentials and bucket access
  before you start.
- Start/Stop button drives a rate-paced write loop: it generates
  realistic customer profiles (name, contact info, address, loyalty
  tier, orders, devices, support tickets, etc.) with `@faker-js/faker`
  and `upsert`s them via the Couchbase SDK, throttled to hit your
  target throughput.
- Live dashboard shows documents generated, throughput, docs/sec, error
  rate, elapsed time, an animated Generator → Agent → Couchbase flow
  diagram, and a throughput sparkline — all pushed over a WebSocket.

## Run locally

```bash
npm install
npm start
```

Then open http://localhost:4300 and go to **Configuration** to connect.

> The `couchbase` package includes native bindings, so `npm install`
> needs a machine with normal build tools (Xcode CLT on macOS, or
> `build-essential` on Linux) — or just use Docker (below), which
> installs everything for you.

## Run with Docker

```bash
docker compose up --build
```

The app will be available at http://localhost:4300. In the config
wizard, point the connection string at a Couchbase cluster reachable
from the container:

- Couchbase running on your host machine: use `couchbase://host.docker.internal`
  (Mac/Windows) as the connection string host.
- Couchbase Capella or a remote cluster: use its connection string as-is,
  and check **Use TLS** (it will use `couchbases://`).
- A local test cluster: uncomment the `couchbase-server` service in
  `docker-compose.yml`, run `docker compose up --build`, visit
  http://localhost:8091 once to finish cluster setup and create a
  bucket, then use `couchbase://couchbase-server` from the generator.

Plain Docker (no compose):

```bash
docker build -t couchbase-data-generator .
docker run -p 4300:4300 couchbase-data-generator
```

## Project layout

```
server.js              Express server, REST API, WebSocket stats broadcast
lib/generatorEngine.js  Couchbase SDK connection + rate-paced write loop
lib/customer360.js      Mock Customer 360 document generator (faker)
public/                 Frontend: wizard + animated dashboard
Dockerfile, docker-compose.yml
```

## API

- `POST /api/test-connection` — `{ connectionString, username, password, useTLS, bucket }`
- `POST /api/generator/start` — same fields + `{ scope, collection, rateMBps }`
- `POST /api/generator/stop`
- `GET /api/generator/status`
- `GET /ws` (WebSocket) — pushes a stats snapshot on every tick

## Notes

- Documents are upserted with the collection's default `customerId` as
  the document key, `type: "customer_profile"`.
- The pacing loop estimates average document size from actual writes
  (exponential moving average) and adjusts batch size every 250ms to
  track the configured MB/s target.
- If a write fails (e.g., bad credentials, unreachable bucket), it's
  counted in the error rate and the most recent error message is shown
  on the dashboard.
