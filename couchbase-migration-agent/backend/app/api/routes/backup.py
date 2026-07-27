"""Backup and rollback controls, callable independently of the full migration pipeline
(e.g. the user wants to re-run a backup, or trigger a manual rollback mid-migration)."""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.migration_engine import MigrationEngine
from app.core.store import MigrationStore
from app.models.enums import MigrationPhase
from app.models.schemas import BackupRecord, RollbackRequest
from app.websocket.progress import broadcast_progress

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{migration_id}/backup", response_model=BackupRecord)
async def trigger_backup(migration_id: UUID):
    store = MigrationStore.instance()
    record = await store.get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    engine = MigrationEngine(on_progress=broadcast_progress)
    record = await engine.backup_source(record)
    if record.phase == MigrationPhase.BACKUP_FAILED:
        raise HTTPException(500, record.error_message or "Backup failed")
    return record.backup_record


@router.post("/rollback", response_model=BackupRecord)
async def rollback(req: RollbackRequest):
    store = MigrationStore.instance()
    record = await store.get(req.migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    engine = MigrationEngine(on_progress=broadcast_progress)
    try:
        record = await engine.rollback(record, req.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not record.backup_record:
        raise HTTPException(500, "Rollback did not produce a backup record")
    return record.backup_record
