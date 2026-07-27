"""
Orchestrates the end-to-end migration pipeline. Two user-selectable replication
modes (MigrationStrategy), chosen in the wizard's "Strategy & Backup" step:

  One-time (BACKUP_RESTORE):
    validate -> backup (source) -> await approval -> cbbackupmgr restore to
    destination -> verify -> COMPLETE. A single snapshot; nothing keeps syncing.

  Continuous (XDCR_LIVE / HYBRID):
    validate -> backup (source) -> await approval -> [HYBRID only: bulk
    cbbackupmgr restore for existing data] -> XDCR replication established ->
    REPLICATING (ongoing) -- stays here, polling replication stats, until the
    user calls stop_replication() to either cut over (-> COMPLETE) or halt
    without cutover (-> STOPPED).

A source backup is always taken first regardless of mode -- that's about
protecting the source, not about how data reaches the destination.

Progress is parsed (best-effort) from cbbackupmgr's stdout for one-time/bulk
transfers, and polled from Couchbase's XDCR task stats for continuous
replication; both are pushed to any registered progress callback (the
websocket layer) so the UI can render live throughput/lag graphs,
AWS-DMS-dashboard style.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.core.backup_manager import BackupManager
from app.core.capella_client import CapellaClient
from app.core.couchbase_client import CouchbaseClusterClient
from app.core.store import MigrationStore
from app.core.validator import MigrationValidator
from app.core.xdcr import XDCRManager
from app.models.enums import CONTINUOUS_STRATEGIES, BackupStatus, MigrationPhase, MigrationStrategy
from app.models.schemas import MigrationRecord, MigrationStats

logger = logging.getLogger(__name__)
settings = get_settings()

ProgressCallback = Callable[[MigrationRecord], Awaitable[None]]

# How often to poll XDCR task stats while a continuous replication is REPLICATING.
REPLICATION_POLL_INTERVAL_S = 5

# cbbackupmgr transfer progress lines look roughly like:
#   "Transferred 1234/5678 items (56.78%), 12.3MB/s"
_PROGRESS_RE = re.compile(
    r"Transferred\s+(?P<done>\d+)/(?P<total>\d+)\s+items.*?(?P<pct>[\d.]+)%\)?,?\s*(?P<rate>[\d.]+)\s*(?P<unit>[KMG]B)/s",
    re.IGNORECASE,
)


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

    # -- pipeline steps -----------------------------------------------------

    async def validate(self, record: MigrationRecord) -> MigrationRecord:
        record.phase = MigrationPhase.VALIDATING
        self._log(record, "Running pre-migration validation checks...")
        await self._emit(record)

        validator = MigrationValidator(record.migration_id, record.plan.source, record.plan.destination)
        report = await asyncio.get_event_loop().run_in_executor(None, validator.run)
        record.validation_report = report
        record.phase = MigrationPhase.VALIDATED if report.passed else MigrationPhase.VALIDATION_FAILED
        self._log(
            record,
            f"Validation {'passed' if report.passed else 'FAILED'} "
            f"({sum(c.passed for c in report.checks)}/{len(report.checks)} checks OK).",
        )
        await self._emit(record)
        return record

    async def backup_source(self, record: MigrationRecord) -> MigrationRecord:
        if record.phase != MigrationPhase.VALIDATED:
            raise ValueError("Cannot back up before validation has passed.")
        record.phase = MigrationPhase.BACKUP_IN_PROGRESS
        self._log(record, "Starting source cluster backup (cbbackupmgr)...")
        await self._emit(record)

        bucket_names = [b.bucket_name for b in record.plan.buckets if b.include] or (
            record.validation_report.source_topology.buckets if record.validation_report and record.validation_report.source_topology else []
        )
        manager = BackupManager(record.migration_id, record.plan.source, bucket_names)
        backup_record = await manager.backup()
        record.backup_record = backup_record

        if backup_record.status != BackupStatus.COMPLETE:
            record.phase = MigrationPhase.BACKUP_FAILED
            record.error_message = backup_record.error_message
            self._log(record, f"Backup FAILED: {backup_record.error_message}")
        else:
            record.phase = MigrationPhase.AWAITING_APPROVAL
            self._log(
                record,
                f"Backup complete ({(backup_record.size_bytes or 0) / (1024**2):.1f} MiB). "
                "Migration is ready for user approval.",
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

    async def run_migration(self, record: MigrationRecord) -> MigrationRecord:
        if record.phase != MigrationPhase.APPROVED:
            raise ValueError("Migration must be approved before it can start.")

        if record.plan.destination.is_capella:
            self._ensure_destination_buckets(record)

        is_continuous = record.plan.strategy in CONTINUOUS_STRATEGIES

        try:
            if record.plan.strategy in (MigrationStrategy.BACKUP_RESTORE, MigrationStrategy.HYBRID):
                await self._run_backup_restore(record)
            if is_continuous:
                await self._start_continuous_replication(record)
        except Exception as exc:  # noqa: BLE001
            record.phase = MigrationPhase.FAILED
            record.error_message = str(exc)
            self._log(record, f"Migration FAILED: {exc}")
            await self._emit(record)
            return record

        if is_continuous:
            # REPLICATING is an ongoing state, not a terminal one: this call blocks
            # here, polling XDCR stats, until stop_replication() (invoked from a
            # separate API request) flips the phase away from REPLICATING.
            return await self._monitor_replication(record)

        record.phase = MigrationPhase.VERIFYING
        self._log(record, "Migration transfer complete; verifying document counts on destination...")
        await self._emit(record)
        await self._verify(record)

        record.phase = MigrationPhase.COMPLETE
        self._log(record, "Migration complete.")
        await self._emit(record)
        return record

    def _ensure_destination_buckets(self, record: MigrationRecord) -> None:
        try:
            capella = CapellaClient()
            created = capella.ensure_buckets_exist(
                record.plan.destination, [b.model_dump() for b in record.plan.buckets if b.include]
            )
            if created:
                self._log(record, f"Auto-provisioned destination buckets: {', '.join(created)}")
        except Exception as exc:  # noqa: BLE001
            self._log(record, f"Destination bucket auto-provisioning skipped: {exc}")

    async def _run_backup_restore(self, record: MigrationRecord) -> None:
        record.phase = MigrationPhase.MIGRATING
        self._log(record, "Restoring backup archive into destination cluster...")
        await self._emit(record)

        assert record.backup_record is not None
        # IMPORTANT: do NOT strip the couchbase://couchbases:// scheme here. Per
        # cbbackupmgr's own HOST FORMATS documentation, http:// and couchbase://
        # both default to unencrypted port 8091, while https:// and couchbases://
        # default to TLS port 18091. Capella requires 18091 -- stripping the scheme
        # previously caused cbbackupmgr to silently fall back to 8091 and fail with
        # "failed to bootstrap client: failed to connect to any host(s)", which
        # looked identical to a hibernated cluster, a bad password, or a missing IP
        # allowlist entry, none of which was the actual problem here.
        dest_host = record.plan.destination.connection_string
        # See ClusterConnectionConfig.use_external_network / BackupManager._cluster_arg()
        # for why this is needed for cloud-hosted (EC2/K8s) clusters: cbbackupmgr can
        # connect fine initially but then fail with "connection refused" reaching a
        # node's internal address for a specific service.
        if record.plan.destination.use_external_network:
            dest_host += "?network=external"
        cbbackupmgr = str(Path(settings.couchbase_bin_dir) / "cbbackupmgr")
        args = [
            cbbackupmgr, "restore",
            "--archive", record.backup_record.archive_path,
            "--repo", record.backup_record.repo_name,
            "--cluster", dest_host,
            "--username", record.plan.destination.username,
            "--password", record.plan.destination.password,
            "--force-updates",
        ]
        # cbbackupmgr requires --cacert or --no-ssl-verify for any TLS (couchbases://)
        # connection -- Capella is always TLS. Mirrors BackupManager._tls_args() /
        # couchbase_client.py's verify=ca_cert_path-or-False pattern: use a configured
        # CA cert when present, otherwise --no-ssl-verify rather than failing outright.
        if record.plan.destination.use_tls:
            if record.plan.destination.ca_cert_path:
                args += ["--cacert", record.plan.destination.ca_cert_path]
            else:
                args += ["--no-ssl-verify"]
        # The backup archive always contains every bucket from the source cluster
        # (cbbackupmgr backup has no per-bucket filter flag -- see the comment in
        # BackupManager.backup()); bucket selection from the wizard is enforced here
        # instead, at restore time, where --include-data is actually supported.
        if record.backup_record.buckets:
            args += ["--include-data", ",".join(record.backup_record.buckets)]
        if record.plan.throttle_mb_per_sec:
            args += ["--data-rate-limit", str(record.plan.throttle_mb_per_sec)]
        args += ["--threads", str(record.plan.parallelism)]

        start = time.monotonic()
        record.stats = MigrationStats(docs_total=record.validation_report.source_topology.total_docs or 0
                                       if record.validation_report and record.validation_report.source_topology else 0)

        # cbbackupmgr needs its bundled libcrypto/libssl on LD_LIBRARY_PATH, which is
        # deliberately not set container-wide (see backend/Dockerfile) -- scope it to
        # just this subprocess, same as BackupManager._run() in backup_manager.py.
        env = {
            **os.environ,
            "LD_LIBRARY_PATH": os.pathsep.join(
                filter(None, [settings.couchbase_lib_dir, os.environ.get("LD_LIBRARY_PATH")])
            ),
        }
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
        )
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line:
                continue
            self._parse_progress_line(record, line, start)
            if len(record.log_tail) < 200:
                self._log(record, line)
            await self._emit(record)

        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"cbbackupmgr restore exited with code {rc}")

        record.stats.docs_migrated = record.stats.docs_total
        record.stats.elapsed_seconds = time.monotonic() - start

    def _parse_progress_line(self, record: MigrationRecord, line: str, start: float) -> None:
        match = _PROGRESS_RE.search(line)
        elapsed = time.monotonic() - start
        record.stats.elapsed_seconds = elapsed
        if match:
            done, total = int(match["done"]), int(match["total"])
            rate = float(match["rate"])
            unit_mul = {"KB": 1 / 1024, "MB": 1, "GB": 1024}[match["unit"].upper()]
            record.stats.docs_migrated = done
            record.stats.docs_total = total or record.stats.docs_total
            record.stats.throughput_mb_per_sec = rate * unit_mul
            record.stats.throughput_docs_per_sec = done / elapsed if elapsed > 0 else 0.0
            remaining = max(total - done, 0)
            record.stats.eta_seconds = (
                remaining / record.stats.throughput_docs_per_sec if record.stats.throughput_docs_per_sec > 0 else None
            )
        elif record.stats.docs_total:
            # Fallback: no machine-parsable line this tick; keep elapsed time fresh so the
            # UI's elapsed/ETA readout doesn't stall even if cbbackupmgr's output format
            # differs by version.
            pass

    async def _start_continuous_replication(self, record: MigrationRecord) -> None:
        self._log(record, "Configuring continuous XDCR replication to destination...")
        await self._emit(record)
        xdcr = XDCRManager(record.plan.source, record.plan.destination)
        bucket_names = [b.bucket_name for b in record.plan.buckets if b.include]
        await xdcr.setup_replications(bucket_names)
        record.phase = MigrationPhase.REPLICATING
        record.stats.replication_active = True
        self._log(record, "Continuous XDCR replication established; source and destination are now syncing.")
        await self._emit(record)

    async def _monitor_replication(self, record: MigrationRecord) -> MigrationRecord:
        xdcr = XDCRManager(record.plan.source, record.plan.destination)
        prev_docs_written = 0
        prev_ts = time.monotonic()

        while True:
            await asyncio.sleep(REPLICATION_POLL_INTERVAL_S)

            # Re-read from the store: stop_replication() runs from a separate API
            # request and mutates the persisted record directly, so pick that up.
            fresh = await self.store.get(record.migration_id)
            if fresh is None or fresh.phase != MigrationPhase.REPLICATING:
                return fresh or record

            record = fresh
            try:
                agg = await asyncio.get_event_loop().run_in_executor(None, xdcr.get_aggregate_stats)
            except Exception as exc:  # noqa: BLE001
                self._log(record, f"Could not poll XDCR stats: {exc}")
                await self._emit(record)
                continue

            now = time.monotonic()
            elapsed = max(now - prev_ts, 0.001)
            docs_delta = max(agg["docs_written"] - prev_docs_written, 0)
            mutations_per_sec = docs_delta / elapsed

            record.stats.replication_active = agg["active"]
            record.stats.changes_left = agg["changes_left"]
            record.stats.mutations_replicated = agg["docs_written"]
            record.stats.mutations_per_sec = mutations_per_sec
            record.stats.last_replication_poll = datetime.utcnow()
            # Approximate "lag" as time-to-drain the current change queue at the
            # observed replication rate -- not a true clock-lag figure, but the
            # closest thing available without per-mutation timestamps.
            record.stats.replication_lag_seconds = (
                agg["changes_left"] / mutations_per_sec if mutations_per_sec > 0 else None
            )
            record.stats.elapsed_seconds += REPLICATION_POLL_INTERVAL_S

            if agg["errors"]:
                self._log(record, f"XDCR reported errors: {agg['errors']}")

            prev_docs_written = agg["docs_written"]
            prev_ts = now
            await self._emit(record)

    async def stop_replication(self, record: MigrationRecord, perform_cutover: bool) -> MigrationRecord:
        if record.phase != MigrationPhase.REPLICATING:
            raise ValueError(f"Migration is not currently replicating (phase: {record.phase}).")

        self._log(
            record,
            "Stopping continuous replication and performing cutover..." if perform_cutover
            else "Stopping continuous replication without cutover...",
        )
        await self._emit(record)

        xdcr = XDCRManager(record.plan.source, record.plan.destination)
        try:
            await asyncio.get_event_loop().run_in_executor(None, xdcr.stop_replications)
        except Exception as exc:  # noqa: BLE001
            self._log(record, f"Warning: error tearing down XDCR replication: {exc}")

        record.stats.replication_active = False

        if perform_cutover:
            record.phase = MigrationPhase.VERIFYING
            self._log(record, "Replication stopped; verifying destination before marking complete...")
            await self._emit(record)
            await self._verify(record)
            record.phase = MigrationPhase.COMPLETE
            self._log(record, "Cutover complete. Destination is now the system of record.")
        else:
            record.phase = MigrationPhase.STOPPED
            self._log(record, "Replication stopped. Source remains the system of record.")

        await self._emit(record)
        return record

    async def _verify(self, record: MigrationRecord) -> None:
        try:
            dest_client = CouchbaseClusterClient(record.plan.destination)
            topo = dest_client.snapshot_topology()
            dest_client.close()
            src_total = record.stats.docs_total
            dest_total = topo.total_docs or 0
            drift = abs(src_total - dest_total)
            self._log(
                record,
                f"Verification: source={src_total} docs, destination={dest_total} docs "
                f"(drift={drift}).",
            )
        except Exception as exc:  # noqa: BLE001
            self._log(record, f"Verification could not complete automatically: {exc}")

    # -- rollback -------------------------------------------------------------

    async def rollback(self, record: MigrationRecord, reason: str) -> MigrationRecord:
        if not record.backup_record or record.backup_record.status != BackupStatus.COMPLETE:
            raise ValueError("No completed backup is available to roll back to.")
        record.phase = MigrationPhase.ROLLING_BACK
        self._log(record, f"Rolling back source cluster from backup (reason: {reason})...")
        await self._emit(record)

        bucket_names = [b.bucket_name for b in record.plan.buckets if b.include]
        manager = BackupManager(record.migration_id, record.plan.source, bucket_names)
        restored = await manager.rollback(record.backup_record)
        record.backup_record = restored

        if restored.status == BackupStatus.RESTORED:
            record.phase = MigrationPhase.ROLLED_BACK
            self._log(record, "Rollback complete; source cluster restored to pre-migration state.")
        else:
            record.phase = MigrationPhase.FAILED
            record.error_message = restored.error_message
            self._log(record, f"Rollback FAILED: {restored.error_message}")

        await self._emit(record)
        return record
