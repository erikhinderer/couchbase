"""
embedding-service: loads Hugging Face `sentence-transformers`-compatible models
on demand and serves inference over a tiny HTTP API. Kept separate from the
main FastAPI backend so model downloads (up to ~2.3GB) and PyTorch's memory
footprint don't run in the same process as the API/websocket event loop, and so
this one service can be handed a GPU independently (see docker-compose.gpu.yml).

Not restricted to the curated top-10 list in the backend's embedding_models
registry -- any valid sentence-transformers model id works here. The backend
owns the curated list (and per-model dimensions/prefix/similarity metric) that
actually gets shown in the UI; this service just executes whatever model_id
it's asked for.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level="INFO")
logger = logging.getLogger("embedding_service")

app = FastAPI(title="Couchbase JSON Vectorizer Agent - Embedding Service", version="1.0.0")

_models: Dict[str, SentenceTransformer] = {}
_lock = threading.Lock()


def _get_model(model_id: str) -> SentenceTransformer:
    with _lock:
        model = _models.get(model_id)
        if model is None:
            logger.info("Loading model %s (first use downloads it from Hugging Face)...", model_id)
            model = SentenceTransformer(model_id)
            _models[model_id] = model
            logger.info("Model %s loaded (dim=%d).", model_id, model.get_sentence_embedding_dimension())
        return model


class WarmupRequest(BaseModel):
    model_id: str


class EmbedRequest(BaseModel):
    model_id: str
    texts: list[str]
    text_prefix: str = ""


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "loaded_models": list(_models.keys())}


@app.post("/warmup")
async def warmup(req: WarmupRequest) -> dict:
    try:
        model = _get_model(req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not load model {req.model_id}: {exc}") from exc
    return {"model_id": req.model_id, "dimensions": model.get_sentence_embedding_dimension()}


@app.post("/embed")
async def embed(req: EmbedRequest) -> dict:
    if not req.texts:
        return {"vectors": [], "dimensions": 0}
    try:
        model = _get_model(req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not load model {req.model_id}: {exc}") from exc

    inputs = [f"{req.text_prefix}{t}" for t in req.texts] if req.text_prefix else req.texts
    vectors = model.encode(inputs, normalize_embeddings=True, show_progress_bar=False)
    return {"vectors": vectors.tolist(), "dimensions": model.get_sentence_embedding_dimension()}
