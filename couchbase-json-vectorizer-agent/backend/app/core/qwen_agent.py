"""
Client for the local Qwen 3.8 LLM (Qwen 3, 8B params, "qwen3:8b"), served via an
Ollama-compatible HTTP API (see qwen-service/ for the container that serves it).

This powers the in-app assistant panel only: explaining validation failures,
recommending an embedding model given the documents' language/size, and
summarizing job health. It does NOT generate the document vector embeddings
themselves -- those come from the user's chosen Hugging Face model, served by
embedding-service (see core/embedding_service_client.py). Keeping the reasoning
LLM local/self-hosted means cluster credentials, document samples, and topology
never leave the Docker network.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Couchbase JSON Vectorizer Agent assistant, embedded in a tool \
that creates real-time vector embeddings for JSON documents stored in Couchbase, and \
validates that Couchbase Vector Search is fully operational against them.

Your job:
- Explain validation failures/warnings in plain language and suggest concrete fixes \
(e.g. "vector search requires Couchbase Enterprise Edition or Capella, 7.6.0+").
- Help the user choose an embedding model given their documents' language, size, and \
throughput needs, from the fixed list of 10 supported Hugging Face models.
- Explain what the dashboard metrics mean (backfill vs. watching for new documents, \
docs/minute, in-progress count).
- Never fabricate document counts or throughput numbers -- only reference figures given \
to you in context.
- Keep responses concise and actionable; this is an operational tool, not a chatbot.
"""


class QwenAgentError(RuntimeError):
    pass


class QwenAgentClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = self.settings.qwen_base_url.rstrip("/")

    async def chat(self, messages: list[dict[str, str]], context: dict[str, Any] | None = None) -> str:
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context:
            full_messages.append({
                "role": "system",
                "content": f"Relevant context for this conversation:\n{context}",
            })
        full_messages += messages

        payload = {
            "model": self.settings.qwen_model_name,
            "messages": full_messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.settings.qwen_request_timeout_s) as client:
            try:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise QwenAgentError(f"Qwen chat request failed: {exc}") from exc
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()

    async def is_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False
