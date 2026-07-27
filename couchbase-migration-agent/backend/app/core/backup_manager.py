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
import shlex
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.models.enums import BackupStatus
from app.models.schemas import BackupRecord, ClusterConnectionConfig

logger = logging.getLogger(__name__)
settings = get_settings()


class BackupError(RuntimeError):
    pass


class BackupManager:
    """Orchestrates cbbackupmgr config/backup/restore lifecycle for one migration."""

    def __init__(self, migration_id: UUID, source: ClusterConnectionConfig, buckets: list[str]):
        self.migration_id = migration_id
        self.source = source
        self.buckets = buckets
        self.repo_name = f"migration-{migration_id}"
        self.archive_path = str(Path(settings.backup_storage_dir) / str(migration_id))
        self._cbbackupmgr = str(Path(settings.couchbase_bin_dir) / "cbbackupmgr")

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
            *self._tls_args(),
        ]

        rc, out, err = await self._run(*args)
        record.completed_at = datetime.utcnow()
        if rc != 0:
            record.status = BackupStatus.FAILED
            record.error_message = err or out
            logger.error("Backup failed for migration %s: %s", self.migration_id, record.error_message)
            return record

        record.status = BackupStatus.COMPLETE
        record.size_bytes = self._dir_size(self.archive_path)
        logger.info("Backup complete for migration %s (%s bytes)", self.migration_id, record.size_bytes)
        return record

    async def rollback(self, record: BackupRecord) -> BackupRecord:
        """Restore the source cluster from the backup archive, undoing any partial
        migration side-effects (e.g. bucket flushes performed as part of cutover)."""
        args = [
            "restore", "--archive", record.archive_path, "--repo", record.repo_name,
            "--cluster", self._cluster_arg(),
            "--username", self.source.username, "--password", self.source.password,
            "--force-updates",
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
