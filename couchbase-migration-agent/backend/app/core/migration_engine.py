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

# cbbackupmgr refuses to restore a scope/collection whose name already exists on the
# destination under a *different* internal id (or vice versa) -- it won't guess whether
# that's coincidence or the same logical collection, and demands an explicit identity
# mapping via --map-data instead. This shows up constantly in a repeated-testing
# workflow like this app's (the same Capella bucket getting torn into / restored into
# across many migration attempts), since Couchbase never reuses a dropped/recreated
# collection's id. Rather than surface this as an opaque failure every time, we parse
# cbbackupmgr's own error text for exactly the bucket/scope/collection it named and
# retry with a literal "same name on both sides" --map-data entry -- the identity-
# mapping form Couchbase's own docs give as the fix for this exact message. See
# _run_backup_restore()'s retry loop below.
_MAP_DATA_COLLECTION_CONFLICT_RE = re.compile(
    r"collection '(?P<collection>[^']+)' with id 0x[0-9a-fA-F]+ in the scope '(?P<scope>[^']+)' "
    r"exists with a different name/id",
)
_MAP_DATA_SCOPE_CONFLICT_RE = re.compile(
    r"scope '(?P<scope>[^']+)' with id 0x[0-9a-fA-F]+ exists with a different name/id",
)
# Safety valve: each retry can only ever add mappings for conflicts cbbackupmgr just
# reported, so this bounds how many distinct scopes/collections we'll auto-reconcile
# before giving up and surfacing whatever error remains. cbbackupmgr only reports ONE
# conflicting scope/collection per failed attempt (it fails fast rather than
# collecting every conflict up front), so this needs to cover the worst-case *count*
# of scopes/collections in a single bucket, not just a couple of retries -- e.g. the
# travel-sample bucket alone has well over a dozen (5x tenant_agent_NN scopes with
# users/bookings collections, plus the inventory scope's airline/airport/hotel/
# landmark/route collections). Each retry only costs cbbackupmgr a near-instant
# fail-fast round trip (no data has transferred yet at that point), so a generous cap
# here is cheap.
_MAX_MAP_DATA_RETRIES = 40


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
        base_args = [
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
                base_args += ["--cacert", record.plan.destination.ca_cert_path]
            else:
                base_args += ["--no-ssl-verify"]
        # Capella's cluster access credentials (the username/password from "Database
        # Access", not a full cluster Administrator) aren't authorized to drive the
        # Analytics/Query/Views portions of the backup service's REST API -- cbbackupmgr
        # still tries to register/transfer that metadata during restore unless told not
        # to, which surfaces as a confusing "authentication error executing 'POST'
        # request to '/api/v1/bucket/<bucket>/backup', check credentials" even though
        # the same credentials are working fine for the KV data restore itself. Per
        # Couchbase's own "Backup a Self-Managed Cluster and Restore to a Capella
        # Provisioned Cluster" guide, restoring self-managed -> Capella needs exactly
        # these five --disable flags (Capella also doesn't support views at all, hence
        # --disable-views regardless of the credential-permission issue). Using the
        # explicit flags here instead of cbbackupmgr 7.6+'s shorter --capella alias
        # since it works across every cbbackupmgr version, matching this app's stated
        # 7.2.0-8.0.2 support range.
        if record.plan.destination.is_capella:
            base_args += [
                "--disable-analytics", "--disable-cluster-analytics",
                "--disable-bucket-query", "--disable-cluster-query",
                "--disable-views",
            ]
        # The backup archive always contains every bucket from the source cluster
        # (cbbackupmgr backup has no per-bucket filter flag -- see the comment in
        # BackupManager.backup()); bucket selection from the wizard is enforced here
        # instead, at restore time, where --include-data is actually supported.
        if record.backup_record.buckets:
            base_args += ["--include-data", ",".join(record.backup_record.buckets)]
        if record.plan.throttle_mb_per_sec:
            base_args += ["--data-rate-limit", str(record.plan.throttle_mb_per_sec)]
        base_args += ["--threads", str(record.plan.parallelism)]

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

        # See _MAP_DATA_COLLECTION_CONFLICT_RE's comment above: a destination bucket
        # that's been restored into before (extremely common while iterating on a real
        # Capella cluster like this) can have scopes/collections whose names match the
        # archive but whose internal ids don't -- cbbackupmgr refuses to guess and asks
        # for --map-data. We detect that from cbbackupmgr's own output and retry with
        # an identity mapping (same name on both sides) rather than failing the whole
        # migration over something this mechanical.
        map_data_pairs: set[str] = set()
        attempt = 0
        while True:
            attempt += 1
            args = list(base_args)
            if map_data_pairs:
                args += ["--map-data", ",".join(sorted(map_data_pairs))]

            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
            )
            assert proc.stdout is not None
            captured_lines: list[str] = []
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                captured_lines.append(line)
                self._parse_progress_line(record, line, start)
                if len(record.log_tail) < 200:
                    self._log(record, line)
                await self._emit(record)

            rc = await proc.wait()
            if rc == 0:
                break

            output_text = "\n".join(captured_lines)
            new_pairs: set[str] = set()
            buckets = record.backup_record.buckets or []
            for m in _MAP_DATA_COLLECTION_CONFLICT_RE.finditer(output_text):
                scope, collection = m["scope"], m["collection"]
                for bucket in buckets:
                    new_pairs.add(f"{bucket}.{scope}.{collection}={bucket}.{scope}.{collection}")
            for m in _MAP_DATA_SCOPE_CONFLICT_RE.finditer(output_text):
                scope = m["scope"]
                for bucket in buckets:
                    new_pairs.add(f"{bucket}.{scope}={bucket}.{scope}")

            truly_new = new_pairs - map_data_pairs
            if not truly_new or attempt >= _MAX_MAP_DATA_RETRIES:
                raise RuntimeError(f"cbbackupmgr restore exited with code {rc}")

            map_data_pairs |= truly_new
            self._log(
                record,
                "Destination already has scope/collection(s) with matching names but "
                "different internal ids (typical after re-running migrations against the "
                "same Capella bucket) -- retrying restore with --map-data to reconcile by "
                f"name: {', '.join(sorted(truly_new))}",
            )
            await self._emit(record)

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
