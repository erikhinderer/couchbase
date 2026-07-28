"""
Backup and rollback of the SOURCE cluster using cbbackupmgr, Couchbase's official
backup/restore tool (bundled with Couchbase Server / the Server Tools package).

The agent always takes a full backup of the source before any data leaves the
building. If the migration fails, or the user cancels, `rollback()` restores the
source from that backup so the source cluster is left exactly as it was found.

This module shells out to the `cbbackupmgr` CLI (see couchbase_bin_dir in config)
rather than re-implementing the backup format -- that binary format is versioned by
Couchbase and only cbbackupmgr itself is guaranteed to read/write it correctly across
7.2.0 - 8.0.2.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.models.enums import BackupStatus
from app.models.schemas import BackupRecord, ClusterConnectionConfig

logger = logging.getLogger(__name__)
settings = get_settings()

# cbbackupmgr's default (non-`--no-progress-bar`) output is an in-place-redrawing
# terminal display, not a single machine-friendly "done/total" line -- each redraw
# emits (at least) two separate lines we can read cleanly once split on "\n":
#   [================================== ] 48.70%
#   Transferring key valu... at 3.78MiB/s (about 2s remaining) 11338 items / 7.57MiB
# (the description text itself is truncated mid-word by cbbackupmgr to fit a
# terminal width, so it's not reliable to match on -- only the trailing numbers are).
# Some status lines omit the "at <rate> (about <eta> remaining)" clause entirely
# (e.g. "Marking transfer as complete 63288 items / 41.49MiB"), so rate/eta and
# items/size are parsed independently rather than as one combined pattern.
_PROGRESS_PCT_RE = re.compile(r"^\[[=\s]*\]\s*(?P<pct>[\d.]+)%\s*$")
_PROGRESS_ITEMS_SIZE_RE = re.compile(
    r"(?P<items>[\d,]+)\s+items\s*/\s*(?P<size>[\d.]+)\s*(?P<unit>[KMGT]i?B)\s*$", re.IGNORECASE,
)
_PROGRESS_RATE_ETA_RE = re.compile(
    r"at\s+(?P<rate>[\d.]+)\s*(?P<unit>[KMGT]i?B)/s\s*\(about\s+(?P<eta>[^)]+?)\s+remaining\)", re.IGNORECASE,
)
# cbbackupmgr reports sizes/rates using binary (Ki/Mi/Gi) units in practice, but
# tolerate the SI spellings too -- both mean the same "1024-based MB" here.
_SIZE_UNIT_TO_MB = {"B": 1 / (1024 * 1024), "KB": 1 / 1024, "KIB": 1 / 1024,
                     "MB": 1.0, "MIB": 1.0, "GB": 1024.0, "GIB": 1024.0,
                     "TB": 1024.0 * 1024, "TIB": 1024.0 * 1024}
# cbbackupmgr renders remaining-time as a Go time.Duration string, e.g. "2s",
# "2m9s", "1h2m3s", "0s".
_GO_DURATION_RE = re.compile(
    r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?$",
)

BackupProgressCallback = Callable[[BackupRecord], Awaitable[None]]


def _parse_go_duration_seconds(text: str) -> float | None:
    m = _GO_DURATION_RE.match(text.strip())
    if not m or not any(m.groups()):
        return None
    hours = int(m.group("h") or 0)
    minutes = int(m.group("m") or 0)
    seconds = float(m.group("s") or 0)
    return hours * 3600 + minutes * 60 + seconds


class BackupError(RuntimeError):
    pass


class BackupThrottleRequested(Exception):
    """Raised out of BackupManager.backup() when a still-running backup was
    deliberately terminated in response to request_abort() -- NOT a real failure.
    Carries the thread count and finding kind that triggered it so the caller
    (MigrationEngine.backup_source()'s auto-throttle loop) can relaunch a fresh
    BackupManager at the lower thread count."""

    def __init__(self, target_threads: int, reason: str):
        super().__init__(f"backup stopped for auto-throttle to {target_threads} threads ({reason})")
        self.target_threads = target_threads
        self.reason = reason


class BackupManager:
    """Orchestrates cbbackupmgr config/backup/restore lifecycle for one migration."""

    def __init__(
        self, migration_id: UUID, source: ClusterConnectionConfig, buckets: list[str],
        on_progress: BackupProgressCallback | None = None, parallelism: int = 1,
        repo_suffix: str = "",
    ):
        self.migration_id = migration_id
        self.source = source
        self.buckets = buckets
        # repo_suffix lets a caller give each auto-throttle restart attempt its own
        # repo/archive (e.g. "-throttle1", "-throttle2") instead of reusing one that a
        # just-killed cbbackupmgr process left mid-write -- cbbackupmgr has no
        # supported way to resume a *backup* (unlike restore's own resumability), so
        # reusing the same repo after an abort risks cbbackupmgr treating it as a
        # corrupt/incomplete backup rather than starting clean. Defaults to "" so
        # every other caller (rollback, a normal single-shot backup) is unaffected.
        self.repo_name = f"migration-{migration_id}{repo_suffix}"
        self.archive_path = str(Path(settings.backup_storage_dir) / f"{migration_id}{repo_suffix}")
        self._cbbackupmgr = str(Path(settings.couchbase_bin_dir) / "cbbackupmgr")
        # Called (if set) with the live BackupRecord every time backup() parses a new
        # progress tick from cbbackupmgr's output, so the caller (MigrationEngine) can
        # push it out over the websocket for the wizard's live progress bar.
        self._on_progress = on_progress
        # Previously omitted entirely, which meant every backup silently ran with
        # whatever cbbackupmgr's own CLI default is, with no way for the wizard's
        # "parallelism" setting (already used for restore's --threads) to affect it,
        # and no real configured value for BottleneckMonitor to compare against
        # Couchbase's own thread-vs-CPU sizing guidance (see bottleneck_detector.py).
        self.parallelism = max(1, parallelism)
        # Set by request_abort() (called from the on_progress callback, so from the
        # same asyncio task that's driving _run_streaming's read loop -- no lock
        # needed) to signal "stop the subprocess and raise BackupThrottleRequested"
        # the next time _run_streaming checks, rather than mid-callback.
        self._abort_request: tuple[int, str] | None = None

    def request_abort(self, target_threads: int, reason: str) -> None:
        """Ask a currently-running backup() to stop cbbackupmgr and raise
        BackupThrottleRequested(target_threads, reason) once it's done so, so the
        caller can relaunch at a lower thread count. Safe to call from within the
        on_progress callback passed to this instance."""
        self._abort_request = (target_threads, reason)

    def _conn_host(self) -> str:
        # IMPORTANT: do NOT strip the couchbase://couchbases:// scheme here. Per
        # cbbackupmgr's own HOST FORMATS documentation, http:// and couchbase://
        # both default to unencrypted port 8091, while https:// and couchbases://
        # default to TLS port 18091. Capella (and any TLS-enabled cluster) requires
        # 18091 -- stripping the scheme previously caused cbbackupmgr to silently
        # fall back to 8091 and fail with "failed to bootstrap client: failed to
        # connect to any host(s)", which looked identical to a hibernated cluster,
        # a bad password, or a missing IP allowlist entry, none of which was the
        # actual problem.
        return self.source.connection_string

    def _cluster_arg(self) -> str:
        """The value to pass to cbbackupmgr's -c/--cluster flag: the full scheme+host
        from _conn_host() (see its docstring for why the scheme must be preserved),
        plus ?network=external when the user has flagged this cluster as needing
        alternate/external addressing (see ClusterConnectionConfig.use_external_network
        -- typical for cloud VMs like EC2 or Kubernetes/CAO, where cbbackupmgr can connect fine
        for the initial handshake but then fail with "connection refused" partway through once
        it needs a node's internal address for a specific service, commonly GSI index defs)."""
        host = self._conn_host()
        return f"{host}?network=external" if self.source.use_external_network else host

    def _tls_args(self) -> list[str]:
        """cbbackupmgr's own docs: 'Either this flag [--cacert] or the --no-ssl-verify
        flag must be specified when using an SSL encrypted connection.' Mirrors the
        verify=self.config.ca_cert_path if ... else False pattern already used in
        couchbase_client.py -- a configured CA cert is used to verify strictly, and
        without one we fall back to --no-ssl-verify rather than failing outright
        (Capella's own cert chain is publicly trusted, but self-signed on-prem/EC2
        clusters commonly aren't, and this app doesn't ship a bundled CA store)."""
        if not self.source.use_tls:
            return []
        if self.source.ca_cert_path:
            return ["--cacert", self.source.ca_cert_path]
        return ["--no-ssl-verify"]

    async def _run(self, *args: str) -> tuple[int, str, str]:
        cmd = [self._cbbackupmgr, *args]
        logger.info("Running: %s", " ".join(shlex.quote(a) for a in cmd if "password" not in a.lower()))
        # cbbackupmgr is dynamically linked against the libcrypto/libssl shipped
        # alongside it in settings.couchbase_lib_dir. That path is deliberately NOT on
        # the container's global LD_LIBRARY_PATH (it's ABI-incompatible with the
        # system OpenSSL Python's own `ssl` module needs -- see backend/Dockerfile),
        # so it's added just for this subprocess's environment instead.
        env = {
            **os.environ,
            "LD_LIBRARY_PATH": os.pathsep.join(
                filter(None, [settings.couchbase_lib_dir, os.environ.get("LD_LIBRARY_PATH")])
            ),
        }
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    async def _run_streaming(self, record: BackupRecord, *args: str) -> tuple[int, str]:
        """Like _run(), but reads cbbackupmgr's stdout line by line (merging stderr
        in) instead of buffering the whole run, parsing progress out of each line and
        pushing it to self._on_progress as it goes -- see the _PROGRESS_*_RE patterns
        above. Returns (exit_code, full_captured_output) once the process exits;
        full_captured_output is still needed on failure since error text (and, for
        restore, the --map-data conflict details) shows up as regular lines here too.

        Also checks request_abort() after every on_progress call: if set, this stops
        cbbackupmgr itself (SIGTERM, then SIGKILL after a grace period if it hasn't
        exited) and raises BackupThrottleRequested instead of returning normally --
        the caller relaunches at a lower thread count rather than treating this as a
        failed backup."""
        cmd = [self._cbbackupmgr, *args]
        logger.info("Running: %s", " ".join(shlex.quote(a) for a in cmd if "password" not in a.lower()))
        env = {
            **os.environ,
            "LD_LIBRARY_PATH": os.pathsep.join(
                filter(None, [settings.couchbase_lib_dir, os.environ.get("LD_LIBRARY_PATH")])
            ),
        }
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
        )
        assert proc.stdout is not None
        start = time.monotonic()
        captured: list[str] = []
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line:
                continue
            captured.append(line)
            if self._on_progress and self._apply_progress_line(record, line, start):
                await self._on_progress(record)
            if self._abort_request is not None:
                await self._terminate(proc)
                target_threads, reason = self._abort_request
                raise BackupThrottleRequested(target_threads, reason)
        rc = await proc.wait()
        return rc, "\n".join(captured)

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Stop a still-running cbbackupmgr process gracefully, falling back to a
        hard kill if it doesn't exit promptly -- used only by the auto-throttle abort
        path above, never on a normal completion/failure."""
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    @staticmethod
    def _apply_progress_line(record: BackupRecord, line: str, start: float) -> bool:
        """Mutates record's progress fields from a single line of cbbackupmgr output,
        if that line carries progress info. Returns True if anything changed (so the
        caller knows whether it's worth emitting a websocket update)."""
        changed = False
        pct_match = _PROGRESS_PCT_RE.match(line)
        if pct_match:
            record.progress_pct = float(pct_match["pct"])
            changed = True
        items_match = _PROGRESS_ITEMS_SIZE_RE.search(line)
        if items_match:
            record.docs_done = int(items_match["items"].replace(",", ""))
            record.size_mb_done = float(items_match["size"]) * _SIZE_UNIT_TO_MB.get(
                items_match["unit"].upper(), 1.0
            )
            changed = True
        rate_match = _PROGRESS_RATE_ETA_RE.search(line)
        if rate_match:
            record.throughput_mb_per_sec = float(rate_match["rate"]) * _SIZE_UNIT_TO_MB.get(
                rate_match["unit"].upper(), 1.0
            )
            record.eta_seconds = _parse_go_duration_seconds(rate_match["eta"])
            changed = True
        if changed:
            record.elapsed_seconds = time.monotonic() - start
        return changed

    async def create_archive(self) -> None:
        os.makedirs(self.archive_path, exist_ok=True)
        rc, out, err = await self._run(
            "config", "--archive", self.archive_path, "--repo", self.repo_name,
        )
        if rc != 0 and "already exists" not in err.lower():
            raise BackupError(f"cbbackupmgr config failed: {err or out}")

    async def backup(self) -> BackupRecord:
        record = BackupRecord(
            migration_id=self.migration_id,
            archive_path=self.archive_path,
            repo_name=self.repo_name,
            buckets=self.buckets,
            status=BackupStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        if self._on_progress:
            await self._on_progress(record)

        await self.create_archive()

        # `cbbackupmgr backup` has no per-bucket filter flag -- `--include-data`/
        # `--exclude-data` only exist on `cbbackupmgr restore` (confirmed against this
        # version's own --help output: the `backup` subcommand's flag list doesn't have
        # either one, and passing --include-data anyway fails hard with "Unknown flag").
        # A backup always captures every bucket in the source cluster, matching what the
        # wizard's copy already says ("A full backup of the source cluster"). Bucket
        # selection is enforced later, at restore time, in MigrationEngine._run_backup_restore.
        args = [
            "backup", "--archive", self.archive_path, "--repo", self.repo_name,
            "--cluster", self._cluster_arg(),
            "--username", self.source.username, "--password", self.source.password,
            "--threads", str(self.parallelism),
            *self._tls_args(),
        ]

        try:
            rc, output = await self._run_streaming(record, *args)
        except BackupThrottleRequested:
            # Not a failure -- the running cbbackupmgr was stopped on purpose so the
            # caller can relaunch a fresh BackupManager at a lower thread count. Mark
            # the record so the UI shows something more informative than "failed" for
            # the brief moment before the new attempt's own progress starts arriving.
            record.status = BackupStatus.THROTTLING
            record.completed_at = datetime.utcnow()
            if self._on_progress:
                await self._on_progress(record)
            raise

        record.completed_at = datetime.utcnow()
        if rc != 0:
            record.status = BackupStatus.FAILED
            record.error_message = output
            logger.error("Backup failed for migration %s: %s", self.migration_id, record.error_message)
            if self._on_progress:
                await self._on_progress(record)
            return record

        record.status = BackupStatus.COMPLETE
        record.progress_pct = 100.0
        record.size_bytes = self._dir_size(self.archive_path)
        logger.info("Backup complete for migration %s (%s bytes)", self.migration_id, record.size_bytes)
        if self._on_progress:
            await self._on_progress(record)
        return record

    async def rollback(self, record: BackupRecord) -> BackupRecord:
        """Restore the source cluster from the backup archive, undoing any partial
        migration side-effects (e.g. bucket flushes performed as part of cutover)."""
        args = [
            "restore", "--archive", record.archive_path, "--repo", record.repo_name,
            "--cluster", self._cluster_arg(),
            "--username", self.source.username, "--password", self.source.password,
            "--force-updates",
            "--threads", str(self.parallelism),
            *self._tls_args(),
        ]
        rc, out, err = await self._run(*args)
        if rc != 0:
            record.status = BackupStatus.FAILED
            record.error_message = f"Rollback failed: {err or out}"
            logger.error(record.error_message)
            return record
        record.status = BackupStatus.RESTORED
        logger.info("Rollback complete for migration %s; source restored from backup.", self.migration_id)
        return record

    @staticmethod
    def _dir_size(path: str) -> int:
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total
