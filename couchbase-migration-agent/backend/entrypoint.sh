#!/usr/bin/env bash
set -e

mkdir -p /data/backups /data/state

# Sanity-check that cbbackupmgr is reachable; warn loudly rather than fail hard so
# the API still comes up for cluster validation / UI browsing even if the tools
# image layer didn't resolve on this platform.
if ! command -v cbbackupmgr >/dev/null 2>&1 && [ ! -x "${COUCHBASE_BIN_DIR:-/opt/couchbase/bin}/cbbackupmgr" ]; then
  echo "WARNING: cbbackupmgr not found at ${COUCHBASE_BIN_DIR:-/opt/couchbase/bin}." >&2
  echo "         Backup/restore/rollback will fail. This image copies cbbackupmgr from the" >&2
  echo "         official couchbase:enterprise-<version> Docker Hub image at build time (see" >&2
  echo "         backend/Dockerfile) -- rebuild with --no-cache if that step was skipped or" >&2
  echo "         pull failed for this platform/architecture." >&2
fi

exec "$@"
