#!/usr/bin/env bash
set -euo pipefail
export HOST=${HOST:-0.0.0.0}
export PORT=${PORT:-8089}
export COUCHBASE_CONNECTION_STRING=${COUCHBASE_CONNECTION_STRING:-couchbase://localhost}
export COUCHBASE_USERNAME=${COUCHBASE_USERNAME:-Administrator}
export COUCHBASE_PASSWORD=${COUCHBASE_PASSWORD:-CouchbaseDemo123!}
export COUCHBASE_BUCKET=${COUCHBASE_BUCKET:-mcp_demo}
export COUCHBASE_SCOPE=${COUCHBASE_SCOPE:-mcp_demo}
export OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://localhost:11434/v1}
export OLLAMA_MODEL=${OLLAMA_MODEL:-llama3.1:8b}
python app.py
