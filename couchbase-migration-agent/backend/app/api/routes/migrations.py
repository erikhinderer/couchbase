"""
Core migration lifecycle endpoints: create a plan, run validation, approve, start,
monitor, and list migrations. This is the primary surface the React wizard and
dashboard talk to.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.migration_engine import MigrationEngine
from app.core.store import MigrationStore
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import MigrationPhase
from app.models.schemas import (
    MigrationApproval,
    MigrationPlanCreate,
    MigrationRecord,
    ReplicationStopRequest,
    ValidationReport,
)
from app.websocket.progress import broadcast_progress

logger = logging.getLogger(__name__)
router = APIRouter()


def _engine() -> MigrationEngine:
    return MigrationEngine(on_progress=broadcast_progress)


@router.post("", response_model=MigrationRecord)
async def create_migration(plan: MigrationPlanCreate) -> MigrationRecord:
    record = MigrationRecord(plan=plan)
    await MigrationStore.instance().save(record)
    return record


@router.get("", response_model=list[MigrationRecord])
async def list_migrations() -> list[MigrationRecord]:
    return await MigrationStore.instance().list_all()


@router.get("/{migration_id}", response_model=MigrationRecord)
async def get_migration(migration_id: UUID) -> MigrationRecord:
    record = await MigrationStore.instance().get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    return record


@router.post("/{migration_id}/validate", response_model=ValidationReport)
async def validate_migration(migration_id: UUID) -> ValidationReport:
    record = await MigrationStore.instance().get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    record = await _engine().validate(record)

    # Persist a memory of this validation outcome for future recall.
    try:
        await AgentMemoryStore.instance().remember(
            "validation_result",
            {
                "migration_name": record.plan.name,
                "passed": record.validation_report.passed if record.validation_report else None,
                "source": record.plan.source.label,
                "destination": record.plan.destination.label,
            },
            migration_id=str(migration_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write validation memory: %s", exc)

    if not record.validation_report:
        raise HTTPException(500, "Validation did not produce a report")
    return record.validation_report


@router.post("/{migration_id}/backup", response_model=MigrationRecord)
async def backup_migration(migration_id: UUID, background_tasks: BackgroundTasks) -> MigrationRecord:
    record = await MigrationStore.instance().get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    if record.phase != MigrationPhase.VALIDATED:
        raise HTTPException(400, f"Cannot back up before validation has passed (current phase: {record.phase}).")

    async def _run() -> None:
        engine = _engine()
        try:
            await engine.backup_source(record)
        except Exception as exc:  # noqa: BLE001
            # backup_source() already turns a failed cbbackupmgr run into a normal
            # BACKUP_FAILED phase + populated backup_record.error_message (surfaced
            # to the wizard via the websocket like any other outcome) -- this only
            # catches something unexpected happening *around* that, e.g. archive
            # directory creation, so a bug there doesn't leave the wizard's progress
            # bar stuck at "running" forever with no explanation.
            logger.exception("Unexpected error backing up migration %s", migration_id)
            record.phase = MigrationPhase.BACKUP_FAILED
            record.error_message = str(exc)
            await engine._emit(record)  # noqa: SLF001

    # Backing up a real cluster routinely takes over a minute (see BackupManager).
    # This used to run inline and block the HTTP response for the whole duration --
    # on at least one real run the browser reported "NetworkError when attempting to
    # fetch resource" partway through even though the backup itself completed
    # successfully server-side moments later (confirmed by the same websocket
    # broadcast this endpoint already pushes progress over). A single request held
    # open that long is exposed to any idle-connection reset along the way (proxies,
    # Docker's port forwarding, browser tab throttling); scheduling it as a
    # background task instead -- the same fix already applied to start_migration()
    # for the same underlying reason -- means this response returns immediately and
    # the wizard's progress bar / final result come entirely from the websocket
    # (see NewMigrationPage.tsx's displayedBackup), which isn't tied to this
    # request's lifetime at all.
    background_tasks.add_task(_run)
    return record


@router.post("/{migration_id}/approve", response_model=MigrationRecord)
async def approve_migration(migration_id: UUID, approval: MigrationApproval) -> MigrationRecord:
    record = await MigrationStore.instance().get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    if approval.migration_id != migration_id:
        raise HTTPException(400, "migration_id mismatch between path and body")
    try:
        record = await _engine().approve(record, approval.approved_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        await AgentMemoryStore.instance().remember(
            "migration_approved",
            {"migration_name": record.plan.name, "approved_by": approval.approved_by},
            migration_id=str(migration_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write approval memory: %s", exc)
    return record


@router.post("/{migration_id}/start", response_model=MigrationRecord)
async def start_migration(migration_id: UUID, background_tasks: BackgroundTasks) -> MigrationRecord:
    record = await MigrationStore.instance().get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    if record.phase != MigrationPhase.APPROVED:
        raise HTTPException(400, f"Migration must be approved before starting (current phase: {record.phase}).")

    async def _run() -> None:
        engine = _engine()
        # For continuous strategies this call blocks until stop_replication() (a
        # separate API request) flips the phase away from REPLICATING, so it can
        # legitimately run for a long time -- that's expected for "continuous".
        finished = await engine.run_migration(record)
        memory_kind = {
            MigrationPhase.COMPLETE: "migration_completed",
            MigrationPhase.STOPPED: "replication_stopped",
        }.get(finished.phase, "migration_failed")
        try:
            await AgentMemoryStore.instance().remember(
                memory_kind,
                {
                    "migration_name": finished.plan.name,
                    "phase": finished.phase.value,
                    "docs_migrated": finished.stats.docs_migrated,
                    "mutations_replicated": finished.stats.mutations_replicated,
                    "error": finished.error_message,
                },
                migration_id=str(migration_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write completion memory: %s", exc)

    # Pass the coroutine FUNCTION itself, not a lambda that calls asyncio.create_task()
    # inside it. Starlette's BackgroundTasks awaits async callables directly in the
    # existing event loop; a plain lambda (even one that calls create_task) isn't
    # itself a coroutine function, so Starlette instead runs it via run_in_threadpool
    # -- in a worker THREAD with no running event loop, where asyncio.create_task()
    # immediately fails with "RuntimeError: no running event loop". That failure
    # happens *after* this response is already sent (background tasks run post-response)
    # and isn't surfaced to the caller at all, so the wizard silently sat at MIGRATING
    # forever with no further log lines -- only visible in `docker compose logs backend`.
    # Deliberately NOT setting record.phase here before scheduling the background task.
    # run_migration() (via _run_backup_restore / _start_continuous_replication) owns the
    # APPROVED -> MIGRATING transition itself, the same way validate() owns its own phase
    # transitions and backup_source() owns its own -- and run_migration() *requires*
    # phase == APPROVED as a precondition. Setting MIGRATING here raced against that: this
    # line ran synchronously before the response was even sent, so by the time the
    # background task actually executed run_migration(), phase was already MIGRATING and
    # its own guard rejected it with "Migration must be approved before it can start."
    # The frontend doesn't need an optimistic update here either -- run_migration() emits
    # the real phase change over the websocket (broadcast_progress) within moments.
    background_tasks.add_task(_run)
    return record


@router.post("/{migration_id}/replication/stop", response_model=MigrationRecord)
async def stop_replication(migration_id: UUID, req: ReplicationStopRequest) -> MigrationRecord:
    """Stop a continuous (XDCR_LIVE / HYBRID) replication that's currently in the
    REPLICATING phase. `perform_cutover=true` finalizes the migration as COMPLETE;
    `perform_cutover=false` halts sync without treating it as finished (source
    stays authoritative)."""
    record = await MigrationStore.instance().get(migration_id)
    if not record:
        raise HTTPException(404, "Migration not found")
    if req.migration_id != migration_id:
        raise HTTPException(400, "migration_id mismatch between path and body")
    try:
        return await _engine().stop_replication(record, req.perform_cutover)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{migration_id}")
async def delete_migration(migration_id: UUID) -> dict:
    await MigrationStore.instance().delete(migration_id)
    return {"deleted": True}
