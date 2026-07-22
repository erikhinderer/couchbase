# Serves Qwen 3 (8B) locally via Ollama's OpenAI/Ollama-compatible HTTP API
# (chat: /api/chat) so the vectorizer agent's assistant reasoning never leaves
# the Docker network.
#
# NOTE: "qwen2.5:3.8b" is not a real Ollama model tag -- Qwen2.5 only ships as
# 0.5b/1.5b/3b/7b/14b/32b/72b, so pulling it fails with "pull model manifest:
# file does not exist". qwen3:8b is an actual published tag, and the closest
# match to "Qwen 3.8" read as "Qwen 3, 8B" -- matching the requirement that
# this agent run on Qwen 3.8.
FROM ollama/ollama:latest

ENV OLLAMA_HOST=0.0.0.0:11434
ENV QWEN_MODEL=qwen3:8b

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
