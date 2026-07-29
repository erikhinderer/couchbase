"""
Orchestrates the end-to-end migration pipeline. Three user-selectable replication
modes (MigrationStrategy), chosen in the wizard's "Destination & Mode" step:

  One-time (FULL_LOAD):
    validate -> await approval -> full load (extract source -> load Couchbase) ->
    verify -> COMPLETE. A single pass; nothing keeps syncing afterward.

  Continuous (CDC_LIVE):
    validate -> await approval -> change-data-capture started immediately ->
    REPLICATING (ongoing) -- stays here, applying change events, until the user
    calls stop_replication() to either cut over (-> COMPLETE) or halt without
    cutover (-> STOPPED). No bulk copy step: the destination only has what CDC
    has captured since it started.

  Hybrid (FULL_LOAD_AND_CDC):
    validate -> await approval -> full load -> change-data-capture takes over for
    the ongoing delta -> REPLICATING, same terminal states as CDC_LIVE.

Unlike the sibling couchbase-migration-agent project, there is no separate backup
step here -- every connector is strictly read-only against the source (see
README.md "Why there's no separate backup step"), so nothing needs protecting
before data starts moving, and "rollback" means undoing the *destination* side
(stop CDC, optionally purge what this migration wrote) rather than restoring the
source from an archive.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.config import get_settings
from app.core.bottleneck_detector import (
    MAX_AUTO_THROTTLE_ATTEMPTS,
    MIN_AUTO_THROTTLE_CONCURRENCY,
    BottleneckMonitor,
)
from app.core.capella_client import CapellaClient
from app.core.connectors.base import ChangeEvent, SourceConnector, SourceDocument
from app.core.connectors.registry import get_connector
from app.core.couchbase_client import CouchbaseClusterClient, sanitize_couchbase_name
from app.core.couchbase_loader import CouchbaseLoader
from app.core.store import MigrationStore
from app.core.validator import MigrationValidator
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import CONTINUOUS_STRATEGIES, BottleneckKind, MigrationPhase, MigrationStrategy
from app.models.schemas import BottleneckFinding, MigrationRecord

logger = logging.getLogger(__name__)
settings = get_settings()

ProgressCallback = Callable[[MigrationRecord], Awaitable[None]]

# How often stats/bottleneck findings are recomputed and emitted while the full
# load or replication loop is running, independent of how often individual
# batches/events complete.
EMIT_INTERVAL_S = 1.0


class MigrationEngine:
    def __init__(self, on_progress: ProgressCallback | None = None):
        self.store = MigrationStore.instance()
        self.on_progress = on_progress

    async def _emit(self, record: MigrationRecord) -> None:
        record.updated_at = datetime.utcnow()
        await self.store.save(record)
        if self.on_progress:
            await self.on_progress(record)

    def _log(self, record: MigrationRecord, line: str) -> None:
        record.log_tail.append(f"[{datetime.utcnow().isoformat(timespec='seconds')}Z] {line}")
        record.log_tail = record.log_tail[-200:]
        logger.info("migration %s: %s", record.migration_id, line)

    def _included_containers(self, record: MigrationRecord) -> list[str]:
        return [c.container_name for c in record.plan.containers if c.include]

    def _target_map(self, record: MigrationRecord) -> dict[str, tuple[str, str]]:
        default_scope = sanitize_couchbase_name(record.plan.source.database or "_default") \
            if record.plan.source.database else "_default"
        out: dict[str, tuple[str, str]] = {}
        for c in record.plan.containers:
            if not c.include:
                continue
            scope = sanitize_couchbase_name(c.target_scope_name) if c.target_scope_name else default_scope
            collection = sanitize_couchbase_name(c.target_collection_name) if c.target_collection_name else \
                sanitize_couchbase_name(c.container_name)
            out[c.container_name] = (scope, collection)
        return out

    def _record_finding(self, record: MigrationRecord, raw, *, phase: str) -> BottleneckFinding:
        finding = BottleneckFinding(
            kind=raw.kind, phase=phase, message=raw.message, suggestion=raw.suggestion,
            recommended_concurrency=raw.recommended_concurrency,
        )
        record.bottleneck_findings.append(finding)
        record.bottleneck_findings = record.bottleneck_findings[-20:]
        self._log(record, f"Bottleneck detected ({raw.kind.value}, {phase}): {raw.message} Suggestion: {raw.suggestion}")
        return finding

    async def _remember(self, kind: str, payload: dict, migration_id) -> None:
        try:
            await AgentMemoryStore.instance().remember(kind, payload, migration_id=str(migration_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write agent memory (%s): %s", kind, exc)

    # -- pipeline steps -----------------------------------------------------

    async def validate(self, record: MigrationRecord) -> MigrationRecord:
        record.phase = MigrationPhase.VALIDATING
        self._log(record, "Running pre-migration validation checks...")
        await self._emit(record)

        validator = MigrationValidator(
            record.migration_id, record.plan.source, record.plan.destination,
            record.plan.strategy, record.plan.containers,
        )
        report = await validator.run()
        record.validation_report = report
        record.phase = MigrationPhase.AWAITING_APPROVAL if report.passed else MigrationPhase.VALIDATION_FAILED
        self._log(
            record,
            f"Validation {'passed' if report.passed else 'FAILED'} "
            f"({sum(c.passed for c in report.checks)}/{len(report.checks)} checks OK).",
        )
        await self._emit(record)
        return record

    async def approve(self, record: MigrationRecord, approved_by: str) -> MigrationRecord:
        if record.phase != MigrationPhase.AWAITING_APPROVAL:
            raise ValueError(f"Migration is not awaiting approval (current phase: {record.phase}).")
        record.phase = MigrationPhase.APPROVED
        record.approved_by = approved_by
        record.approved_at = datetime.utcnow()
        self._log(record, f"Migration approved by {approved_by}.")
        await self._emit(record)
        return record

    def _ensure_destination_bucket(self, record: MigrationRecord) -> None:
        try:
            if record.plan.destination.is_capella:
                created = CapellaClient().ensure_bucket_exists(
                    record.plan.destination, record.plan.destination_bucket,
                    ram_quota_mb=record.plan.destination_bucket_ram_quota_mb,
                )
            else:
                client = CouchbaseClusterClient(record.plan.destination)
                try:
                    created = client.ensure_bucket(
                        record.plan.destination_bucket, ram_quota_mb=record.plan.destination_bucket_ram_quota_mb,
                    )
                finally:
                    client.close()
            if created:
                self._log(record, f"Auto-provisioned destination bucket '{record.plan.destination_bucket}'.")
                time.sleep(2)  # let the bucket warm up before scope/collection calls
        except Exception as exc:  # noqa: BLE001
            self._log(record, f"Destination bucket auto-provisioning skipped: {exc}")

    async def run_migration(self, record: MigrationRecord) -> MigrationRecord:
        if record.phase != MigrationPhase.APPROVED:
            raise ValueError("Migration must be approved before it can start.")

        await asyncio.to_thread(self._ensure_destination_bucket, record)

        dest_client = CouchbaseClusterClient(record.plan.destination)
        connector = get_connector(record.plan.source)
        containers = self._included_containers(record)
        loader = CouchbaseLoader(
            dest_client, record.plan.destination_bucket, record.migration_id,
            self._target_map(record), concurrency=record.plan.concurrency,
        )

        try:
            await loader.ensure_collections(containers)

            if record.plan.strategy in (MigrationStrategy.FULL_LOAD, MigrationStrategy.FULL_LOAD_AND_CDC):
                await self._run_full_load(record, connector, loader, containers)
                if record.phase == MigrationPhase.FAILED:
                    return record

            is_continuous = record.plan.strategy in CONTINUOUS_STRATEGIES
            if is_continuous:
                record.phase = MigrationPhase.REPLICATING
                record.stats.replication_active = True
                self._log(record, "Starting continuous change-data-capture replication...")
                await self._emit(record)
                return await self._monitor_cdc(record, connector, loader, containers)

            record.phase = MigrationPhase.VERIFYING
            self._log(record, "Migration transfer complete; verifying document counts on destination...")
            await self._emit(record)
            await self._verify(record, loader, containers)
            record.phase = MigrationPhase.COMPLETE
            self._log(record, "Migration complete.")
            await self._emit(record)
            return record
        except Exception as exc:  # noqa: BLE001
            record.phase = MigrationPhase.FAILED
            record.error_message = str(exc)
            self._log(record, f"Migration FAILED: {exc}")
            await self._emit(record)
            return record
        finally:
            dest_client.close()
            await connector.close()

    async def _run_full_load(
        self, record: MigrationRecord, connector: SourceConnector, loader: CouchbaseLoader, containers: list[str],
    ) -> None:
        record.phase = MigrationPhase.MIGRATING
        self._log(record, f"Starting full load of {len(containers)} container(s)...")
        await self._emit(record)

        record.stats.docs_total = sum(
            c.estimated_count or 0
            for c in (record.validation_report.source_topology.containers if record.validation_report and
                      record.validation_report.source_topology else [])
            if c.name in containers
        )

        monitor = BottleneckMonitor(current_concurrency=record.plan.concurrency)
        current_concurrency = record.plan.concurrency
        throttle_attempts = 0
        start = time.monotonic()
        last_emit = 0.0
        per_container_done: dict[str, int] = {c: 0 for c in containers}

        async def _sink(batch: list[SourceDocument]) -> None:
            nonlocal last_emit, throttle_attempts, current_concurrency
            result = await loader.write_batch(batch)
            record.stats.docs_migrated += result.docs_written
            record.stats.docs_failed += result.docs_failed
            record.stats.bytes_migrated += result.bytes_written
            for doc in batch:
                per_container_done[doc.container] = per_container_done.get(doc.container, 0) + 1
            for c, n in per_container_done.items():
                record.stats.per_container.setdefault(c, {})["docs_migrated"] = n

            elapsed = time.monotonic() - start
            record.stats.elapsed_seconds = elapsed
            docs_per_sec = record.stats.docs_migrated / elapsed if elapsed > 0 else 0.0
            record.stats.throughput_docs_per_sec = docs_per_sec
            if record.stats.docs_total:
                remaining = max(record.stats.docs_total - record.stats.docs_migrated, 0)
                record.stats.eta_seconds = remaining / docs_per_sec if docs_per_sec > 0 else None
            if result.latencies_ms:
                record.stats.avg_latency_ms = sum(result.latencies_ms) / len(result.latencies_ms)
            total_done = record.stats.docs_migrated + record.stats.docs_failed
            record.stats.error_rate_pct = (
                (record.stats.docs_failed / total_done * 100.0) if total_done else 0.0
            )

            monitor.observe_throughput(docs_per_sec, elapsed)
            monitor.observe_batch_result(result.docs_written, result.docs_failed)
            for raw in monitor.poll():
                finding = self._record_finding(record, raw, phase="initial_load")
                if (
                    raw.recommended_concurrency is not None
                    and throttle_attempts < MAX_AUTO_THROTTLE_ATTEMPTS
                    and raw.recommended_concurrency < current_concurrency
                ):
                    throttle_attempts += 1
                    old = current_concurrency
                    current_concurrency = max(MIN_AUTO_THROTTLE_CONCURRENCY, raw.recommended_concurrency)
                    loader.set_concurrency(current_concurrency)
                    monitor.current_concurrency = current_concurrency
                    finding.auto_remediated = True
                    finding.suggestion = f"Reduced concurrency from {old} to {current_concurrency}."
                    self._log(record, f"Auto-throttling: reduced concurrency from {old} to {current_concurrency} "
                                       f"({raw.kind.value}, attempt {throttle_attempts}/{MAX_AUTO_THROTTLE_ATTEMPTS}).")
                    await self._remember("bottleneck_auto_remediated", {
                        "migration_name": record.plan.name, "phase": "initial_load",
                        "kind": raw.kind.value, "old_concurrency": old, "new_concurrency": current_concurrency,
                    }, record.migration_id)

            now = time.monotonic()
            if now - last_emit >= EMIT_INTERVAL_S:
                last_emit = now
                await self._emit(record)

        await connector.extract(containers, _sink, batch_size=settings.default_batch_size)
        record.stats.elapsed_seconds = time.monotonic() - start
        self._log(
            record,
            f"Full load complete: {record.stats.docs_migrated} document(s) migrated, "
            f"{record.stats.docs_failed} failed.",
        )
        await self._emit(record)

    async def _monitor_cdc(
        self, record: MigrationRecord, connector: SourceConnector, loader: CouchbaseLoader, containers: list[str],
    ) -> MigrationRecord:
        start = time.monotonic()
        mutations = 0
        last_emit = 0.0

        async for event in connector.stream_changes(containers, record.checkpoint):
            fresh = await self.store.get(record.migration_id)
            if fresh is None or fresh.phase != MigrationPhase.REPLICATING:
                return fresh or record
            record = fresh

            if event.op == "heartbeat":
                continue
            if event.op == "upsert" and event.document:
                result = await loader.write_batch([event.document])
            elif event.op == "delete" and event.key:
                result = await loader.delete_batch(event.container, [event.key])
            else:
                continue

            mutations += result.docs_written
            record.stats.mutations_replicated += result.docs_written
            record.stats.docs_failed += result.docs_failed
            if event.checkpoint is not None:
                record.checkpoint[event.container] = event.checkpoint

            elapsed = max(time.monotonic() - start, 0.001)
            record.stats.mutations_per_sec = mutations / elapsed
            record.stats.last_replication_poll = datetime.utcnow()

            now = time.monotonic()
            if now - last_emit >= EMIT_INTERVAL_S:
                last_emit = now
                await self._emit(record)

        return record

    async def stop_replication(self, record: MigrationRecord, perform_cutover: bool) -> MigrationRecord:
        if record.phase != MigrationPhase.REPLICATING:
            raise ValueError(f"Migration is not currently replicating (phase: {record.phase}).")

        self._log(
            record,
            "Stopping continuous replication and performing cutover..." if perform_cutover
            else "Stopping continuous replication without cutover...",
        )
        record.stats.replication_active = False
        # _monitor_cdc()'s loop re-reads the record from the store on every event/
        # heartbeat and returns as soon as phase != REPLICATING, so flipping the
        # phase here is what actually stops it (no cancellation needed) -- the
        # daemon thread(s) backing the connector's change-stream cursor are left
        # running in the background; see util.bridge_blocking_batches' docstring.
        if perform_cutover:
            record.phase = MigrationPhase.VERIFYING
            await self._emit(record)
            dest_client = CouchbaseClusterClient(record.plan.destination)
            loader = CouchbaseLoader(
                dest_client, record.plan.destination_bucket, record.migration_id,
                self._target_map(record), concurrency=record.plan.concurrency,
            )
            try:
                await self._verify(record, loader, self._included_containers(record))
            finally:
                dest_client.close()
            record.phase = MigrationPhase.COMPLETE
            self._log(record, "Cutover complete. Couchbase is now the system of record.")
        else:
            record.phase = MigrationPhase.STOPPED
            self._log(record, "Replication stopped. Source remains the system of record.")

        await self._emit(record)
        return record

    async def _verify(self, record: MigrationRecord, loader: CouchbaseLoader, containers: list[str]) -> None:
        cluster = loader.client.connect()
        dest_total = 0
        for container in containers:
            scope, collection = loader._target_for(container)  # noqa: SLF001
            try:
                res = cluster.query(
                    f"SELECT RAW COUNT(*) FROM `{loader.bucket_name}`.`{scope}`.`{collection}` AS d "
                    f"WHERE d._migration.migration_id = $migration_id",
                    migration_id=str(record.migration_id),
                )
                dest_total += next(iter(res), 0)
            except Exception as exc:  # noqa: BLE001
                self._log(record, f"Verification query failed for {scope}.{collection}: {exc}")

        src_total = record.stats.docs_migrated + record.stats.mutations_replicated
        drift = abs(src_total - dest_total)
        self._log(
            record,
            f"Verification: documents written by this migration={src_total}, "
            f"found in destination={dest_total} (drift={drift}).",
        )

    # -- rollback -------------------------------------------------------------

    async def rollback(self, record: MigrationRecord, reason: str, purge_destination_data: bool) -> MigrationRecord:
        record.phase = MigrationPhase.ROLLING_BACK
        self._log(record, f"Rolling back migration (reason: {reason})...")
        await self._emit(record)

        if record.phase == MigrationPhase.ROLLING_BACK and purge_destination_data:
            dest_client = CouchbaseClusterClient(record.plan.destination)
            loader = CouchbaseLoader(
                dest_client, record.plan.destination_bucket, record.migration_id,
                self._target_map(record), concurrency=record.plan.concurrency,
            )
            try:
                deleted = await loader.purge_migration(self._included_containers(record))
                self._log(record, f"Purged {deleted} document(s) this migration wrote to Couchbase.")
            except Exception as exc:  # noqa: BLE001
                record.phase = MigrationPhase.FAILED
                record.error_message = str(exc)
                self._log(record, f"Rollback FAILED: {exc}")
                await self._emit(record)
                return record
            finally:
                dest_client.close()

        record.phase = MigrationPhase.ROLLED_BACK
        record.stats.replication_active = False
        self._log(
            record,
            "Rollback complete. The source database was never modified by this migration "
            "(every connector is read-only), so there is nothing to restore there.",
        )
        await self._emit(record)
        return record
