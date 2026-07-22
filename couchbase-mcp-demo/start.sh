#!/usr/bin/env bash
# start.sh - Launches the Couchbase MCP Tool Filtering Demo and shows a live
# status table of what's still starting vs. what's ready, instead of a wall
# of interleaved container logs.
#
# Usage: ./start.sh
# Ctrl-C stops watching (the containers keep running in the background,
# same as any `docker compose up -d`). Re-run `./start.sh` any time to
# resume watching, or `docker compose down` to stop everything.

set -uo pipefail
cd "$(dirname "$0")"

SERVICES="couchbase couchbase-init ollama ollama-pull app"

label_for() {
  case "$1" in
    couchbase)      echo "Couchbase Server" ;;
    couchbase-init) echo "Couchbase provisioning" ;;
    ollama)         echo "Ollama runtime" ;;
    ollama-pull)    echo "Ollama model pull" ;;
    app)            echo "Demo app" ;;
    *)              echo "$1" ;;
  esac
}

# Prints one of: pending | created | running | healthy | unhealthy | done | failed:<code>
service_status() {
  local svc="$1"
  local cid
  cid="$(docker compose ps -q "$svc" 2>/dev/null)"
  if [ -z "$cid" ]; then
    echo "pending"
    return
  fi

  local state
  state="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"
  if [ -z "$state" ]; then
    echo "pending"
    return
  fi

  if [ "$state" = "exited" ]; then
    local code
    code="$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null)"
    if [ "$code" = "0" ]; then
      echo "done"
    else
      echo "failed:${code:-?}"
    fi
    return
  fi

  local health
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null)"
  if [ -n "$health" ]; then
    echo "$health"
  else
    echo "$state"
  fi
}

icon_for() {
  case "$1" in
    healthy|done)      echo "[ OK ]" ;;
    failed:*|unhealthy) echo "[FAIL]" ;;
    pending)            echo "[ .. ]" ;;
    *)                  echo "[....]" ;;
  esac
}

echo "Building images and starting containers (this can take a while on first run)..."
docker compose up -d --build

trap 'echo; echo "Stopped watching - containers are still running. Re-run ./start.sh to watch again, or docker compose down to stop them."; exit 0' INT

INTERACTIVE=0
[ -t 1 ] && INTERACTIVE=1

while true; do
  if [ "$INTERACTIVE" = "1" ]; then
    clear
  else
    echo "------------------------------------------------------------"
  fi

  echo "Couchbase MCP Demo - startup status"
  echo "===================================="

  pending_count=0
  failed_count=0
  status_line=""

  for svc in $SERVICES; do
    st="$(service_status "$svc")"
    printf "  %s %-24s %s\n" "$(icon_for "$st")" "$(label_for "$svc")" "$st"
    case "$st" in
      healthy|done) ;;
      failed:*|unhealthy) failed_count=$((failed_count + 1)) ;;
      *) pending_count=$((pending_count + 1)) ;;
    esac
  done

  echo ""

  if [ "$failed_count" -gt 0 ]; then
    echo "One or more services failed to start."
    echo "Check details with: docker compose logs <service-name>"
    exit 1
  fi

  if [ "$pending_count" -eq 0 ]; then
    echo "All services are up. Open http://localhost:8089"
    echo "(Couchbase Web Console: http://localhost:8091)"
    break
  fi

  sleep 3
done
