# Serves Qwen 3 (8B) locally via Ollama's OpenAI/Ollama-compatible HTTP API
# (chat: /api/chat, embeddings: /api/embeddings) so the migration agent's
# reasoning and vector-memory embeddings never leave the Docker network.
#
# NOTE: "qwen2.5:3.8b" (an earlier value here) is not a real Ollama model tag --
# Qwen2.5 only ships as 0.5b/1.5b/3b/7b/14b/32b/72b, so pulling it fails with
# "pull model manifest: file does not exist". qwen3:8b is an actual published
# tag and the closest match to "Qwen 3.8" read as "Qwen 3, 8B".
FROM ollama/ollama:latest

# --- Corporate TLS inspection support -------------------------------------
# Unlike the other services, this one needs the corporate root CA trusted
# inside the *runtime* image, not just at build time: entrypoint.sh's `ollama
# pull` call happens when the container starts, and it talks to
# registry.ollama.ai / ollama.com over HTTPS -- which fails with "certificate
# signed by unknown authority" under Netskope (or similar TLS-inspecting)
# networks unless this image trusts its root CA too. Left empty (the
# default), this is a no-op. `certs` here is the additional build context
# wired up in docker-compose.yml, since this service's own build context is
# ./qwen-service, not the repo root.
COPY --from=certs corporate-ca.crt /usr/local/share/ca-certificates/corporate-ca.crt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

ENV OLLAMA_HOST=0.0.0.0:11434
ENV QWEN_MODEL=qwen3:8b

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
