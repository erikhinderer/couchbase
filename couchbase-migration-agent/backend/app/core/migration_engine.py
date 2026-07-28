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
import shutil
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.core.backup_manager import (
    BackupManager,
    BackupThrottleRequested,
    _PROGRESS_ITEMS_SIZE_RE,
    _PROGRESS_PCT_RE,
    _PROGRESS_RATE_ETA_RE,
    _SIZE_UNIT_TO_MB,
    _parse_go_duration_seconds,
)
from app.core.bottleneck_detector import (
    MAX_AUTO_THROTTLE_ATTEMPTS,
    MIN_AUTO_THROTTLE_THREADS,
    BottleneckMonitor,
    NodeResourceStats,
)
from app.core.capella_client import CapellaClient
from app.core.couchbase_client import CouchbaseClusterClient
from app.core.store import MigrationStore
from app.core.validator import MigrationValidator
from app.core.xdcr import XDCRManager
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import CONTINUOUS_STRATEGIES, BackupStatus, BottleneckKind, MigrationPhase, MigrationStrategy
from app.models.schemas import BackupRecord, BottleneckFinding, ClusterConnectionConfig, MigrationRecord, MigrationStats

logger = logging.getLogger(__name__)
settings = get_settings()

ProgressCallback = Callable[[MigrationRecord], Awaitable[None]]

# How often to poll XDCR task stats while a continuous replication is REPLICATING.
REPLICATION_POLL_INTERVAL_S = 5

# cbbackupmgr's real progress output is NOT a single "done/total (pct%) rate" line --
# it's an in-place-redrawing terminal display split across separate lines (a bare
# percentage-bar line, and a status line with rate/eta/items/size that doesn't always
# include all of those fields). See backup_manager.py's _PROGRESS_*_RE patterns and
# _apply_progress_line() for the full explanation and the shared regexes/unit tables
# reused here so restore and backup progress parsing stay consistent.

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

    # -- bottleneck detection -------------------------------------------------
    # See bottleneck_detector.py's module docstring for the full design rationale.
    # _check_bottlenecks() itself only ever detects and posts a suggestion finding
    # -- it never touches a running process. backup_source()'s auto-throttle loop
    # (below) is the one place that acts on a finding's recommended_threads, and
    # only for the backup phase.

    @staticmethod
    def _fetch_node_resource_stats(cluster: ClusterConnectionConfig) -> list[NodeResourceStats]:
        """Best-effort: some clusters (notably Capella) don't expose this level of
        node detail over the management REST API at all, or the configured
        credentials may not have the cluster-admin role it requires -- that's an
        expected, non-fatal gap (source clusters are almost always self-managed and
        do expose it), not a bug, so callers should treat an empty result the same
        as "couldn't check this time"."""
        client = CouchbaseClusterClient(cluster)
        try:
            pools = client.get_pools_default()
        finally:
            client.close()
        stats: list[NodeResourceStats] = []
        for n in pools.get("nodes", []):
            sys_stats = n.get("systemStats") or {}
            cpu_cores = n.get("cpuCount") or sys_stats.get("cpu_cores_available") or 0
            mem_total = sys_stats.get("mem_total") or 0
            if not mem_total:
                # Nothing usable on this node -- skip rather than let a 0/0 divide
                # downstream masquerade as "0% memory free".
                continue
            stats.append(NodeResourceStats(
                hostname=n.get("hostname", "unknown"),
                cpu_utilization_pct=float(sys_stats.get("cpu_utilization_rate") or 0.0),
                cpu_cores=int(cpu_cores),
                mem_total_bytes=int(mem_total),
                mem_free_bytes=int(sys_stats.get("mem_free") or 0),
            ))
        return stats

    async def _check_bottlenecks(
        self, record: MigrationRecord, monitor: BottleneckMonitor, *, phase: str,
        cluster: ClusterConnectionConfig, configured_threads: int, mbps: float, elapsed_s: float,
    ) -> list[BottleneckFinding]:
        """Runs one round of bottleneck detection, appends any new findings to
        record.bottleneck_findings, and returns just those new findings so a caller
        (currently only backup_source()'s auto-throttle loop) can act on
        finding.recommended_threads without re-deriving it."""
        monitor.observe(mbps, elapsed_s)
        raw_findings = monitor.local_findings()

        if monitor.should_poll_stats():
            try:
                # Node stats are a synchronous `requests` call (see couchbase_client.py) --
                # push it to a thread so a slow/unreachable cluster can't stall the
                # backup/restore progress loop this is called from.
                node_stats = await asyncio.to_thread(self._fetch_node_resource_stats, cluster)
                raw_findings += monitor.stats_findings(node_stats, configured_threads, cluster.label)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Bottleneck stats poll skipped for %s: %s", cluster.label, exc)

        if not raw_findings:
            return []

        new_findings: list[BottleneckFinding] = []
        for raw in raw_findings:
            finding = BottleneckFinding(
                kind=raw.kind, phase=phase, cluster_label=cluster.label,
                message=raw.message, suggestion=raw.suggestion,
                recommended_threads=raw.recommended_threads,
            )
            new_findings.append(finding)
            record.bottleneck_findings.append(finding)
            record.bottleneck_findings = record.bottleneck_findings[-20:]
            self._log(record, f"Bottleneck detected ({raw.kind.value}, {phase}): {raw.message} Suggestion: {raw.suggestion}")
            try:
                await AgentMemoryStore.instance().remember(
                    "bottleneck_detected",
                    {
                        "migration_name": record.plan.name,
                        "phase": phase,
                        "cluster": cluster.label,
                        "kind": raw.kind.value,
                        "message": raw.message,
                        "suggestion": raw.suggestion,
                    },
                    migration_id=str(record.migration_id),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to write bottleneck-detection memory: %s", exc)
        return new_findings

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

        # Auto-throttle loop: a thread-actionable bottleneck finding on the BACKUP
        # phase (CPU saturation / thread oversubscription / memory pressure on the
        # source cluster) doesn't just get posted as a suggestion here -- since this
        # is a subprocess the app itself launched and fully controls, it stops
        # cbbackupmgr and relaunches at Couchbase's own recommended thread count
        # instead, capped at MAX_AUTO_THROTTLE_ATTEMPTS restarts and never below
        # MIN_AUTO_THROTTLE_THREADS. Each attempt gets a fresh archive/repo (see
        # BackupManager's repo_suffix) since cbbackupmgr has no supported way to
        # resume a *backup* that was killed mid-write. If attempts run out while the
        # cluster is still saturated, the loop just stops throttling and lets the
        # current attempt run to completion/failure -- the last posted finding
        # stands as a plain suggestion for the user, same as any other bottleneck
        # this app can't act on by itself.
        current_threads = max(1, record.plan.parallelism)
        throttle_attempts = 0
        backup_record: BackupRecord | None = None

        while True:
            bottleneck_monitor = BottleneckMonitor()
            throttle_requested = False

            async def _on_backup_progress(backup_rec: BackupRecord) -> None:
                nonlocal throttle_requested
                # Same instance is mutated in place by BackupManager on every parsed
                # progress line -- re-assigning here just keeps record.backup_record
                # pointing at it from the very first (still-RUNNING) tick, so the
                # wizard's websocket subscriber sees live percent/ETA/throughput as
                # the backup runs, not just the final result.
                record.backup_record = backup_rec
                new_findings = await self._check_bottlenecks(
                    record, bottleneck_monitor, phase="backup", cluster=record.plan.source,
                    configured_threads=current_threads,
                    mbps=backup_rec.throughput_mb_per_sec, elapsed_s=backup_rec.elapsed_seconds,
                )
                if (
                    not throttle_requested
                    and throttle_attempts < MAX_AUTO_THROTTLE_ATTEMPTS
                    and current_threads > MIN_AUTO_THROTTLE_THREADS
                ):
                    for finding in new_findings:
                        if finding.recommended_threads is None:
                            continue
                        target = max(
                            MIN_AUTO_THROTTLE_THREADS,
                            min(finding.recommended_threads, current_threads - 1),
                        )
                        if target < current_threads:
                            throttle_requested = True
                            manager.request_abort(target, finding.kind.value)
                            break
                await self._emit(record)

            manager = BackupManager(
                record.migration_id, record.plan.source, bucket_names,
                on_progress=_on_backup_progress, parallelism=current_threads,
                repo_suffix=f"-throttle{throttle_attempts}" if throttle_attempts else "",
            )

            try:
                backup_record = await manager.backup()
            except BackupThrottleRequested as abort:
                throttle_attempts += 1
                old_threads = current_threads
                current_threads = max(MIN_AUTO_THROTTLE_THREADS, abort.target_threads)
                self._log(
                    record,
                    f"Auto-throttling backup: reduced --threads from {old_threads} to "
                    f"{current_threads} on {record.plan.source.label} due to sustained "
                    f"{abort.reason.replace('_', ' ')} (attempt {throttle_attempts}/"
                    f"{MAX_AUTO_THROTTLE_ATTEMPTS}); restarting the backup now.",
                )
                remediation = BottleneckFinding(
                    kind=BottleneckKind(abort.reason),
                    phase="backup",
                    cluster_label=record.plan.source.label,
                    message=(
                        f"Backup threads were oversubscribing {record.plan.source.label}, so "
                        f"the agent stopped the backup and is restarting it with fewer threads."
                    ),
                    suggestion=f"Reduced --threads from {old_threads} to {current_threads} and restarted the backup on a fresh archive.",
                    recommended_threads=current_threads,
                    auto_remediated=True,
                )
                record.bottleneck_findings.append(remediation)
                record.bottleneck_findings = record.bottleneck_findings[-20:]
                try:
                    await AgentMemoryStore.instance().remember(
                        "bottleneck_auto_remediated",
                        {
                            "migration_name": record.plan.name,
                            "phase": "backup",
                            "cluster": record.plan.source.label,
                            "kind": abort.reason,
                            "old_threads": old_threads,
                            "new_threads": current_threads,
                            "attempt": throttle_attempts,
                        },
                        migration_id=str(record.migration_id),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to write auto-remediation memory: %s", exc)
                await self._emit(record)
                # Best-effort cleanup of the aborted attempt's partial archive -- it's
                # never used again (the next attempt gets its own repo_suffix) and
                # leaving it around just wastes disk.
                try:
                    shutil.rmtree(manager.archive_path, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass
                continue
            else:
                break

        assert backup_record is not None
        record.backup_record = backup_record

        if backup_record.status != BackupStatus.COMPLETE:
            record.phase = MigrationPhase.BACKUP_FAILED
            record.error_message = backup_record.error_message
            self._log(record, f"Backup FAILED: {backup_record.error_message}")
        else:
            record.phase = MigrationPhase.AWAITING_APPROVAL
            throttle_note = (
                f" (auto-throttled {throttle_attempts}x to {current_threads} threads)"
                if throttle_attempts else ""
            )
            self._log(
                record,
                f"Backup complete ({(backup_record.size_bytes or 0) / (1024**2):.1f} MiB){throttle_note}. "
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
        bottleneck_monitor = BottleneckMonitor()

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
                await self._check_bottlenecks(
                    record, bottleneck_monitor, phase="restore", cluster=record.plan.destination,
                    configured_threads=record.plan.parallelism,
                    mbps=record.stats.throughput_mb_per_sec, elapsed_s=record.stats.elapsed_seconds,
                )
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
        # NOTE: cbbackupmgr's real output is split across separate lines (a bare
        # percentage-bar line, plus a status line whose rate/eta/items/size clauses
        # aren't all always present) -- see backup_manager.py's _PROGRESS_*_RE
        # patterns, which are reused here so restore and backup parse the exact same
        # way. A previous version of this method matched against a single-line
        # "Transferred X/Y items (Z%), rate" format that cbbackupmgr doesn't actually
        # emit, so throughput/ETA silently stayed at 0/blank for every restore.
        changed = False
        pct_match = _PROGRESS_PCT_RE.match(line)
        if pct_match and record.stats.docs_total:
            # cbbackupmgr's own percentage accounts for bucket-config/GSI/FTS/manifest
            # phases too, not just KV items -- approximate docs_migrated from it
            # against the known total so the UI's "X / Y docs" readout moves smoothly
            # even between item-count ticks.
            record.stats.docs_migrated = min(
                record.stats.docs_total, round(record.stats.docs_total * float(pct_match["pct"]) / 100.0)
            )
            changed = True
        items_match = _PROGRESS_ITEMS_SIZE_RE.search(line)
        if items_match:
            record.stats.docs_migrated = int(items_match["items"].replace(",", ""))
            size_mb = float(items_match["size"]) * _SIZE_UNIT_TO_MB.get(items_match["unit"].upper(), 1.0)
            record.stats.bytes_migrated = round(size_mb * 1024 * 1024)
            changed = True
        rate_match = _PROGRESS_RATE_ETA_RE.search(line)
        if rate_match:
            record.stats.throughput_mb_per_sec = float(rate_match["rate"]) * _SIZE_UNIT_TO_MB.get(
                rate_match["unit"].upper(), 1.0
            )
            record.stats.eta_seconds = _parse_go_duration_seconds(rate_match["eta"])
            changed = True
        if changed:
            elapsed = time.monotonic() - start
            record.stats.elapsed_seconds = elapsed
            record.stats.throughput_docs_per_sec = (
                record.stats.docs_migrated / elapsed if elapsed > 0 else 0.0
            )

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
        # Couchbase's cluster-manager bucket stats (basicStats.itemCount, which
        # snapshot_topology() reads) refresh on an internal aggregation interval and
        # can lag several seconds behind a bulk cbbackupmgr restore that just
        # finished writing tens of thousands of docs. Checking exactly once,
        # immediately after restore, previously logged "destination=0 docs
        # (drift=63343)" on migrations that had in fact fully transferred --
        # confirmed moments later directly in the Capella UI. Poll briefly instead
        # of trusting the very first read; stop as soon as the destination catches
        # up to the source, or after a bounded number of attempts either way.
        src_total = record.stats.docs_total
        dest_total = 0
        last_exc: Exception | None = None
        attempts = 8
        for attempt in range(1, attempts + 1):
            try:
                dest_client = CouchbaseClusterClient(record.plan.destination)
                topo = dest_client.snapshot_topology()
                dest_client.close()
                dest_total = topo.total_docs or 0
                last_exc = None
                if dest_total >= src_total or attempt == attempts:
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == attempts:
                    break
            await asyncio.sleep(2)

        if last_exc is not None and dest_total == 0:
            self._log(record, f"Verification could not complete automatically: {last_exc}")
            return

        drift = abs(src_total - dest_total)
        self._log(
            record,
            f"Verification: source={src_total} docs, destination={dest_total} docs "
            f"(drift={drift}).",
        )

    # -- rollback -------------------------------------------------------------

    async def rollback(self, record: MigrationRecord, reason: str) -> MigrationRecord:
        if not record.backup_record or record.backup_record.status != BackupStatus.COMPLETE:
            raise ValueError("No completed backup is available to roll back to.")
        record.phase = MigrationPhase.ROLLING_BACK
        self._log(record, f"Rolling back source cluster from backup (reason: {reason})...")
        await self._emit(record)

        bucket_names = [b.bucket_name for b in record.plan.buckets if b.include]
        manager = BackupManager(
            record.migration_id, record.plan.source, bucket_names, parallelism=record.plan.parallelism,
        )
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
