"""
Client for the local Qwen 3.8 LLM, served via an Ollama-compatible HTTP API
(see qwen-service/ for the container that serves it). Provides both chat
completion (agentic reasoning / user-facing assistant) and text embeddings (used by
memory/couchbase_memory.py for vector search over agent memory).

Keeping the LLM entirely local/self-hosted means the onboarding agent never has to
send source database credentials, schema samples, or data to a third-party API --
a hard requirement for a tool that handles production database credentials across
five different source systems.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Couchbase Onboarding Agent, an expert assistant embedded in a \
migration tool that moves data from MongoDB, Amazon DynamoDB, Redis, Apache Cassandra, and \
Microsoft Azure Cosmos DB into Couchbase Server (Enterprise Edition) or Couchbase Capella.

Your job:
- Explain validation failures and warnings in plain language and suggest concrete fixes.
- Reason about migration strategy (one-time full load vs. continuous CDC vs. hybrid) given the
  source database's size, whether it supports change capture, and downtime tolerance.
- Explain source-to-Couchbase data modeling decisions: how MongoDB documents, DynamoDB items,
  Redis keys, Cassandra rows, and Cosmos DB items each map to a Couchbase JSON document, key,
  scope, and collection.
- Flag risk before the user approves a migration (e.g. a source type whose CDC mechanism isn't
  enabled/available, documents that may exceed Couchbase's 20MiB document size limit, naming
  collisions once container names are sanitized into Couchbase collection names).
- Never fabricate source statistics -- only reference numbers provided to you in context.
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
