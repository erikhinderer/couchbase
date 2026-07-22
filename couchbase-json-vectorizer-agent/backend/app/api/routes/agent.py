"""Conversational endpoint for the assistant panel: answers questions about a
vectorizer job, grounding responses in the job's actual validation report / stats."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.qwen_agent import QwenAgentClient
from app.core.store import JobStore
from app.models.schemas import AgentChatRequest, AgentChatResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=AgentChatResponse)
async def chat(req: AgentChatRequest) -> AgentChatResponse:
    context: dict = {}

    if req.job_id:
        record = await JobStore.instance().get(req.job_id)
        if not record:
            raise HTTPException(404, "Job not found")
        context["job_name"] = record.plan.name
        context["phase"] = record.phase.value
        context["embedding_model"] = record.plan.embedding_model
        context["stats"] = record.stats.model_dump()
        if record.validation_report:
            context["validation_summary"] = [
                {"check": c.label, "passed": c.passed, "message": c.message}
                for c in record.validation_report.checks
            ]

    client = QwenAgentClient()
    try:
        reply = await client.chat([{"role": "user", "content": req.message}], context=context)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Local LLM (Qwen 3.8) unavailable: {exc}") from exc

    return AgentChatResponse(reply=reply)
