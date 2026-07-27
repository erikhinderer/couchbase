"""
Client for the local Qwen 3.8 LLM, served via an Ollama-compatible HTTP API
(see qwen-service/ for the container that serves it). Provides both chat
completion (agentic reasoning / user-facing assistant) and text embeddings
(used by memory/couchbase_memory.py for vector search over agent memory).

Keeping the LLM entirely local/self-hosted means the migration agent never has to
send cluster topology, credentials, or data samples to a third-party API -- a hard
requirement for a tool that handles production database credentials.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Couchbase Migration Agent, an expert assistant embedded in a \
migration tool that moves Couchbase Server clusters (versions 7.2.0-8.0.2), including \
multi-cluster and XDCR-replicated deployments, to Couchbase Capella.

Your job:
- Explain validation failures and warnings in plain language and suggest concrete fixes.
- Reason about migration strategy (backup/restore vs XDCR live vs hybrid) given cluster
  topology, size, and downtime tolerance.
- Flag risk before the user approves a migration (e.g. missing XDCR reconfiguration,
  version incompatibilities, insufficient destination capacity).
- Never fabricate cluster statistics -- only reference numbers provided to you in context.
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

    async def embed(self, text: str) -> list[float]:
        payload = {"model": self.settings.qwen_embedding_model_name, "prompt": text}
        async with httpx.AsyncClient(timeout=self.settings.qwen_request_timeout_s) as client:
            try:
                resp = await client.post(f"{self.base_url}/api/embeddings", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise QwenAgentError(f"Qwen embedding request failed: {exc}") from exc
        data = resp.json()
        embedding = data.get("embedding", [])
        if not embedding:
            raise QwenAgentError("Qwen returned an empty embedding vector.")
        return embedding

    async def is_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False
