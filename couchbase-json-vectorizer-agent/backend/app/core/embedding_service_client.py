"""
Client for `embedding-service`, the sibling container that actually loads the
user-selected Hugging Face model (via `sentence-transformers`) and runs
inference. Kept as a separate service (rather than importing torch/
sentence-transformers directly into the FastAPI backend process) so:

  1. The backend stays light and starts instantly; the embedding service can be
     scaled/restarted/given a GPU independently.
  2. Model downloads (the largest is ~2.3GB for BGE-M3) and PyTorch's memory
     footprint don't compete with the API/event-loop process.
  3. docker-compose.gpu.yml can hand this one service a GPU without touching
     anything else.
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingServiceError(RuntimeError):
    pass


class EmbeddingServiceClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.embedding_service_base_url.rstrip("/")

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            return resp.json()

    async def warmup(self, model_id: str) -> dict:
        """Pre-load a model into memory (called at job launch time) so the first
        real document doesn't pay a multi-second cold-load penalty."""
        async with httpx.AsyncClient(timeout=180) as client:
            try:
                resp = await client.post(f"{self.base_url}/warmup", json={"model_id": model_id})
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                raise EmbeddingServiceError(f"Failed to warm up model {model_id}: {exc}") from exc

    async def embed(self, model_id: str, texts: list[str], text_prefix: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model_id": model_id, "texts": texts, "text_prefix": text_prefix or ""}
        timeout = self.settings.embedding_request_timeout_s
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(f"{self.base_url}/embed", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingServiceError(f"Embedding request failed for {model_id}: {exc}") from exc
        data = resp.json()
        vectors = data.get("vectors")
        if not vectors:
            raise EmbeddingServiceError("Embedding service returned no vectors.")
        return vectors
