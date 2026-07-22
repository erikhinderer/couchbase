#!/usr/bin/env bash
set -e

# Start the Ollama server in the background, then pull the Qwen model on first
# boot (cached in the ollama_data volume thereafter -- see docker-compose.yml).
ollama serve &
SERVER_PID=$!

echo "Waiting for Ollama server to be ready..."
# Use the `ollama` CLI itself rather than curl -- the official ollama/ollama image
# doesn't ship curl, so a curl-based wait loop (or Docker HEALTHCHECK) would hang.
until ollama list >/dev/null 2>&1; do
  sleep 1
done

if ! ollama list | grep -q "${QWEN_MODEL}"; then
  echo "Pulling ${QWEN_MODEL} (first run only, cached afterwards)..."
  ollama pull "${QWEN_MODEL}"
fi

wait $SERVER_PID
