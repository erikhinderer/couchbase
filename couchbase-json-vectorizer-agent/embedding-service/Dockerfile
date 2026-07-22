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

EXPOSE 8500

HEALTHCHECK --interval=15s --timeout=10s --retries=10 --start-period=20s \
    CMD curl -sf http://localhost:8500/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]
