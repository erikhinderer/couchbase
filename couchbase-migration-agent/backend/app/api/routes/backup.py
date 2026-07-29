"""Backup and rollback controls, callable independently of the full migration pipeline
(e.g. the user wants to re-run a backup, or trigger a manual rollback mid-migration)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.core.migration_engine import MigrationEngine
from app.core.store import MigrationStore
from app.models.enums import BackupStatus, MigrationPhase
from app.models.schemas import BackupRecord, RollbackRequest
from app.websocket.progress import broadcast_progress

logger = logging.getLogger(__name__)
router = APIRouter()

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


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


@router.get("/{migration_id}/download")
async def download_backup(migration_id: UUID):
    """Streams a zip of the migration's completed backup archive, for the wizard's
    "Download backup" button (an optional, extra safety net alongside the
    server-side backup/rollback the migration already relies on -- see the
    "*download optional" note in the UI).

    Only offered once the backup is COMPLETE -- a still-running or failed backup's
    archive directory is either incomplete or was never valid, and zipping it up
    would just hand the user a broken file with no indication anything was wrong."""
    store = MigrationStore.instance()
    record = await store.get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    if not record.backup_record or record.backup_record.status != BackupStatus.COMPLETE:
        raise HTTPException(400, "No completed backup is available to download for this migration.")

    archive_dir = record.backup_record.archive_path
    if not os.path.isdir(archive_dir):
        raise HTTPException(404, "Backup archive no longer exists on disk.")

    # Zip into a scratch temp dir, distinct from the archive itself, so cleanup
    # after the response is sent can't ever touch the real backup. shutil.make_archive
    # is synchronous and a multi-GB archive can take a while to compress -- run it in
    # a thread so it doesn't block the event loop for every other in-flight request
    # (the same asyncio.to_thread pattern already used for node-stats polling in
    # migration_engine.py's bottleneck checks).
    tmp_dir = tempfile.mkdtemp(prefix="cma-backup-dl-")
    safe_name = _SAFE_NAME_RE.sub("-", record.plan.name.strip()) or "migration"
    zip_base = os.path.join(tmp_dir, f"{safe_name}-backup-{migration_id}")
    try:
        zip_path = await asyncio.to_thread(shutil.make_archive, zip_base, "zip", archive_dir)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("Failed to zip backup archive for migration %s", migration_id)
        raise HTTPException(500, f"Failed to prepare backup archive for download: {exc}") from exc

    def _cleanup() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{safe_name}-backup-{migration_id}.zip",
        background=BackgroundTask(_cleanup),
    )


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
