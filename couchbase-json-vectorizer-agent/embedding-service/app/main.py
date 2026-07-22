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
import os
import threading
import time
from typing import Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level="INFO")
logger = logging.getLogger("embedding_service")

# Make sure the Hugging Face cache directory exists and is writable *before* the
# first download attempt -- if the mounted volume comes up owned by a different
# uid, or /data doesn't exist yet, SentenceTransformer's first call fails with a
# raw OSError that previously surfaced to the UI as an opaque "400 Bad Request"
# with no explanation (see backend/app/core/embedding_service_client.py for the
# matching fix that now at least shows whatever this raises).
_HF_CACHE_DIR = os.environ.get("HF_HOME", "/data/hf-cache")
os.makedirs(_HF_CACHE_DIR, exist_ok=True)

app = FastAPI(title="Couchbase JSON Vectorizer Agent - Embedding Service", version="1.0.0")

_models: Dict[str, SentenceTransformer] = {}
_lock = threading.Lock()

# A few models in the curated top-10 list (notably nomic-ai/nomic-embed-text-v1
# and v1.5) ship a custom `modeling_*.py` in their Hugging Face repo and raise
# "This model requires you to execute custom code" unless trust_remote_code is
# set -- so it's on for every model here rather than maintained as a per-model
# list. All 10 curated models are Anthropic/backend-selected, well-known public
# repos, not arbitrary user input, so this is a reasonable default.
_TRUST_REMOTE_CODE = True

# Hugging Face Hub downloads inside a fresh container occasionally fail on the
# very first attempt (DNS not yet warm, a transient connection reset) even
# though the network is fine a moment later -- retry once before giving up
# instead of making every job launch's validation step racy.
_LOAD_RETRIES = 2
_LOAD_RETRY_DELAY_S = 3


def _get_model(model_id: str) -> SentenceTransformer:
    with _lock:
        model = _models.get(model_id)
        if model is not None:
            return model

        last_exc: Exception | None = None
        for attempt in range(1, _LOAD_RETRIES + 1):
            try:
                logger.info(
                    "Loading model %s (attempt %d/%d; first use downloads it from Hugging Face into %s)...",
                    model_id, attempt, _LOAD_RETRIES, _HF_CACHE_DIR,
                )
                model = SentenceTransformer(model_id, trust_remote_code=_TRUST_REMOTE_CODE)
                _models[model_id] = model
                logger.info("Model %s loaded (dim=%d).", model_id, model.get_sentence_embedding_dimension())
                return model
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                # Full traceback goes to the container's own logs (`docker compose
                # logs embedding-service`) even though the API only returns a
                # short message -- this is what to check if the short message
                # alone isn't enough to diagnose a failure.
                logger.exception("Failed to load model %s on attempt %d/%d", model_id, attempt, _LOAD_RETRIES)
                if attempt < _LOAD_RETRIES:
                    time.sleep(_LOAD_RETRY_DELAY_S)
        assert last_exc is not None
        raise last_exc


class WarmupRequest(BaseModel):
    model_id: str


class EmbedRequest(BaseModel):
    model_id: str
    texts: list[str]
    text_prefix: str = ""


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "loaded_models": list(_models.keys())}


def _load_error_detail(model_id: str, exc: Exception) -> str:
    return f"Could not load model {model_id}: {type(exc).__name__}: {exc}"


@app.post("/warmup")
async def warmup(req: WarmupRequest) -> dict:
    try:
        model = _get_model(req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_load_error_detail(req.model_id, exc)) from exc
    return {"model_id": req.model_id, "dimensions": model.get_sentence_embedding_dimension()}


@app.post("/embed")
async def embed(req: EmbedRequest) -> dict:
    if not req.texts:
        return {"vectors": [], "dimensions": 0}
    try:
        model = _get_model(req.model_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_load_error_detail(req.model_id, exc)) from exc

    inputs = [f"{req.text_prefix}{t}" for t in req.texts] if req.text_prefix else req.texts
    vectors = model.encode(inputs, normalize_embeddings=True, show_progress_bar=False)
    return {"vectors": vectors.tolist(), "dimensions": model.get_sentence_embedding_dimension()}
