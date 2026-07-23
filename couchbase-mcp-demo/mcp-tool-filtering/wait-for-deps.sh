#!/bin/sh
# wait-for-deps.sh - blocks until Couchbase and Ollama are genuinely ready,
# then execs the app.
#
# This is a defense-in-depth backstop alongside docker-compose's own
# depends_on conditions. depends_on works correctly for a full
# `docker compose up`, but this container can also get started other ways -
# Docker Desktop's GUI start/stop toggle, a plain `docker start`, or
# `docker compose start` after an interrupted run - none of which are
# guaranteed to replay depends_on's health/completion waits. Running this
# wait loop as the container's own entrypoint means the app only ever starts
# serving traffic once its dependencies are actually ready, regardless of
# how the container itself got started.

set -u

CB_HOST="${COUCHBASE_HOST_FOR_WAIT:-couchbase}"
CB_USER="${COUCHBASE_USERNAME:-Administrator}"
CB_PASS="${COUCHBASE_PASSWORD:-CouchbaseDemo123!}"
CB_BUCKET="${COUCHBASE_BUCKET:-mcp_demo}"
OLLAMA_HOST="${OLLAMA_HOST_FOR_WAIT:-ollama}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b}"
MAX_WAIT_SECONDS="${WAIT_FOR_DEPS_TIMEOUT:-900}"

elapsed=0

wait_for() {
  description="$1"
  shift
  until "$@" > /dev/null 2>&1; do
    if [ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]; then
      echo "[wait-for-deps] Timed out after ${MAX_WAIT_SECONDS}s waiting for ${description}." >&2
      exit 1
    fi
    sleep 3
    elapsed=$((elapsed + 3))
    if [ $((elapsed % 30)) -eq 0 ]; then
      echo "[wait-for-deps] Still waiting for ${description}... (${elapsed}s elapsed)"
    fi
  done
  echo "[wait-for-deps] ${description}: ready."
}

echo "[wait-for-deps] Waiting for Couchbase Server, bucket '${CB_BUCKET}', and Ollama model '${OLLAMA_MODEL}'..."

# No -f here on purpose: this Couchbase Server version returns 401 on
# unauthenticated /pools, which curl -f would treat as a permanent failure
# regardless of how healthy the server actually is. We only need to confirm
# the HTTP server is answering at all; a real connection failure/timeout
# still fails this check correctly without -f.
wait_for "Couchbase Server web console" curl -s -o /dev/null "http://${CB_HOST}:8091/pools"
wait_for "Couchbase bucket '${CB_BUCKET}'" curl -sf -u "${CB_USER}:${CB_PASS}" "http://${CB_HOST}:8091/pools/default/buckets/${CB_BUCKET}"
wait_for "Ollama API" curl -sf "http://${OLLAMA_HOST}:11434/api/tags"

echo "[wait-for-deps] Waiting for Ollama model '${OLLAMA_MODEL}' to finish pulling..."
until curl -sf "http://${OLLAMA_HOST}:11434/api/tags" 2>/dev/null | grep -qF "${OLLAMA_MODEL}"; do
  if [ "$elapsed" -ge "$MAX_WAIT_SECONDS" ]; then
    echo "[wait-for-deps] Timed out after ${MAX_WAIT_SECONDS}s waiting for model '${OLLAMA_MODEL}'." >&2
    exit 1
  fi
  sleep 3
  elapsed=$((elapsed + 3))
  if [ $((elapsed % 30)) -eq 0 ]; then
    echo "[wait-for-deps] Still waiting for model '${OLLAMA_MODEL}'... (${elapsed}s elapsed)"
  fi
done
echo "[wait-for-deps] Ollama model '${OLLAMA_MODEL}': ready."

echo "[wait-for-deps] All dependencies ready - starting app."
exec python app.py
