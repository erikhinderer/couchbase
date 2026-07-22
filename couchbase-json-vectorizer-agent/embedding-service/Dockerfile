# CPU by default -- works out of the box on macOS/Windows Docker Desktop. For an
# NVIDIA GPU on Linux, run with the optional override:
#   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hf-cache

# Created at build time so the directory (and its ownership) exists even before
# the named volume is mounted over it -- app/main.py also creates it defensively
# at startup, but doing it here too avoids relying solely on that. Also disable
# hf_transfer's accelerated-download path: it requires the separate `hf_transfer`
# package, which isn't installed here, and huggingface_hub raises an ImportError
# if HF_HUB_ENABLE_HF_TRANSFER is ever set truthy without it present.
RUN mkdir -p /data/hf-cache
ENV HF_HUB_ENABLE_HF_TRANSFER=0

EXPOSE 8500

HEALTHCHECK --interval=15s --timeout=10s --retries=10 --start-period=20s \
    CMD curl -sf http://localhost:8500/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]
