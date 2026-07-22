"""Vectorizer job lifecycle: create -> validate -> launch -> (stop). The wizard's
four steps map directly to these calls: step 1-3 fill in a VectorizerJobCreate,
step 4 ("Launch agent") calls create -> validate -> launch in sequence."""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.store import JobStore
from app.core.validator import validate_job
from app.core.vectorizer_engine import VectorizerEngine
from app.models.enums import JobPhase
from app.models.schemas import VectorizerJobCreate, VectorizerJobRecord
from app.websocket.progress import broadcast_progress

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=VectorizerJobRecord)
async def create_job(plan: VectorizerJobCreate) -> VectorizerJobRecord:
    record = VectorizerJobRecord(plan=plan, phase=JobPhase.DRAFT)
    await JobStore.instance().save(record)
    return record


@router.get("", response_model=list[VectorizerJobRecord])
async def list_jobs() -> list[VectorizerJobRecord]:
    return await JobStore.instance().list_all()


@router.get("/{job_id}", response_model=VectorizerJobRecord)
async def get_job(job_id: UUID) -> VectorizerJobRecord:
    record = await JobStore.instance().get(job_id)
    if not record:
        raise HTTPException(404, "Job not found")
    return record


@router.post("/{job_id}/validate", response_model=VectorizerJobRecord)
async def validate(job_id: UUID) -> VectorizerJobRecord:
    record = await JobStore.instance().get(job_id)
    if not record:
        raise HTTPException(404, "Job not found")

    record.phase = JobPhase.VALIDATING
    await JobStore.instance().save(record)

    report = await validate_job(job_id, record.plan)
    record.validation_report = report
    record.phase = JobPhase.READY if report.passed else JobPhase.VALIDATION_FAILED
    record.updated_at = datetime.utcnow()
    await JobStore.instance().save(record)
    return record


@router.post("/{job_id}/launch", response_model=VectorizerJobRecord)
async def launch(job_id: UUID) -> VectorizerJobRecord:
    record = await JobStore.instance().get(job_id)
    if not record:
        raise HTTPException(404, "Job not found")
    if not record.validation_report or not record.validation_report.passed:
        raise HTTPException(400, "Job must pass validation before launch")

    await VectorizerEngine.instance().start(job_id, on_progress=broadcast_progress)
    record = await JobStore.instance().get(job_id)
    return record


@router.post("/{job_id}/stop", response_model=VectorizerJobRecord)
async def stop(job_id: UUID) -> VectorizerJobRecord:
    record = await JobStore.instance().get(job_id)
    if not record:
        raise HTTPException(404, "Job not found")
    await VectorizerEngine.instance().stop(job_id)
    record = await JobStore.instance().get(job_id)
    return record
