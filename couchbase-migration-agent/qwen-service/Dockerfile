# Serves Qwen 3 (8B) locally via Ollama's OpenAI/Ollama-compatible HTTP API
# (chat: /api/chat, embeddings: /api/embeddings) so the migration agent's
# reasoning and vector-memory embeddings never leave the Docker network.
#
# NOTE: "qwen2.5:3.8b" (an earlier value here) is not a real Ollama model tag --
# Qwen2.5 only ships as 0.5b/1.5b/3b/7b/14b/32b/72b, so pulling it fails with
# "pull model manifest: file does not exist". qwen3:8b is an actual published
# tag and the closest match to "Qwen 3.8" read as "Qwen 3, 8B".
FROM ollama/ollama:latest

ENV OLLAMA_HOST=0.0.0.0:11434
ENV QWEN_MODEL=qwen3:8b

# Trust an optional corporate/proxy root CA before entrypoint.sh pulls the
# model at container startup. Unlike backend/frontend, this isn't a build-time
# `pip`/`npm install` issue -- `ollama pull` runs at *runtime* against
# registry.ollama.ai/ollama.com, and Ollama (a Go binary) verifies TLS against
# the OS's system cert pool. Baking the corporate cert into
# /etc/ssl/certs/ca-certificates.crt here (ollama/ollama:latest is
# ubuntu:24.04-based) makes it part of the image, so it's already trusted by
# the time the container starts and pulls the model. Otherwise, behind an
# SSL-inspecting proxy this fails with:
#   tls: failed to verify certificate: x509: certificate signed by unknown authority
# Exported by scripts/setup-corporate-ca.sh into qwen-service/certs/; a no-op
# if that hasn't been run / isn't needed.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN if find /usr/local/share/ca-certificates/extra -name '*.crt' 2>/dev/null | grep -q .; then \
      apt-get update && \
      apt-get install -y --no-install-recommends ca-certificates && \
      update-ca-certificates && \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
