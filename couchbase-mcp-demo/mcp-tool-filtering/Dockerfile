FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# sentence-transformers depends on torch, and torch's default PyPI wheel for
# Linux bundles NVIDIA CUDA/cuDNN/Triton libraries (multiple GB) meant for
# GPU inference. This app only runs a small CPU embedding model in this
# container - the actual LLM inference happens in the separate Ollama
# container - so install the official CPU-only torch build first; pip then
# sees torch already satisfied and skips the CUDA variant entirely.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x wait-for-deps.sh

EXPOSE 8089

# wait-for-deps.sh blocks until Couchbase and Ollama are actually ready,
# then execs `python app.py`. This is a backstop alongside docker-compose's
# depends_on conditions - see wait-for-deps.sh for why both are needed.
CMD ["./wait-for-deps.sh"]
