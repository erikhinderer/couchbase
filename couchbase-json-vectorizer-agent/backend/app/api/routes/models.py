"""Read-only endpoint listing the 10 supported Hugging Face embedding models."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.embedding_models import list_models
from app.models.schemas import EmbeddingModelInfo

router = APIRouter()


@router.get("", response_model=list[EmbeddingModelInfo])
async def get_models() -> list[EmbeddingModelInfo]:
    return list_models()
