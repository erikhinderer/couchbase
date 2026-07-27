"""
Cross Data Center Replication (XDCR) handling.

Three responsibilities:

1. Detection (used by the validator/topology snapshot): read a source cluster's
   existing XDCR remotes/replications so multi-datacenter topologies are surfaced
   in the UI's cluster diagram rather than silently migrating only one leg of a
   replicated deployment.

2. Setup: create a new XDCR remote-cluster reference + continuous replication from
   the source into the Capella/destination cluster. Used for two user-selectable
   replication modes (see MigrationStrategy):
     - XDCR_LIVE (continuous, no bulk copy): pure ongoing sync.
     - HYBRID (continuous, after a bulk backup/restore): ongoing delta sync that
       picks up where the one-time bulk copy left off.
   Either way the replication is left running -- the migration sits in the
   REPLICATING phase -- until the user stops it or performs a cutover.

3. Teardown: cancel the replication(s) and remove the remote cluster reference,
   either as part of a cutover (replication is no longer needed, destination is
   now authoritative) or a plain stop (user wants to halt sync without treating
   the migration as complete).
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import requests

from app.core.couchbase_client import CouchbaseClusterClient
from app.models.schemas import ClusterConnectionConfig

logger = logging.getLogger(__name__)

REMOTE_CLUSTER_NAME = "capella-migration-target"


class XDCRError(RuntimeError):
    pass


class XDCRManager:
    def __init__(self, source: ClusterConnectionConfig, destination: ClusterConnectionConfig):
        self.source = source
        self.destination = destination
        self._client = CouchbaseClusterClient(source)

    def _dest_host_port(self) -> str:
        host = (
            self.destination.connection_string
            .replace("couchbases://", "").replace("couchbase://", "").split(",")[0]
        )
        port = "18091" if (self.destination.use_tls or self.destination.is_capella) else "8091"
        return f"{host}:{port}"

    async def setup_replications(self, buckets: list[str]) -> None:
        self._create_remote_cluster_ref(REMOTE_CLUSTER_NAME)
        for bucket in buckets:
            self._create_replication(REMOTE_CLUSTER_NAME, bucket, bucket)

    def _create_remote_cluster_ref(self, remote_name: str) -> None:
        url = f"{self._mgmt_base_url()}/pools/default/remoteClusters"
        payload = {
            "name": remote_name,
            "hostname": self._dest_host_port(),
            "username": self.destination.username,
            "password": self.destination.password,
            "demandEncryption": 1 if (self.destination.use_tls or self.destination.is_capella) else 0,
            "secureType": "full" if self.destination.is_capella else "half",
        }
        resp = requests.post(
            url, data=payload,
            auth=(self.source.username, self.source.password),
            verify=False, timeout=20,
        )
        if resp.status_code not in (200, 201) and "already exists" not in resp.text.lower():
            raise XDCRError(f"Failed to create XDCR remote cluster reference: {resp.text}")
        logger.info("XDCR remote cluster reference '%s' ready.", remote_name)

    def _create_replication(self, remote_name: str, source_bucket: str, target_bucket: str) -> None:
        url = f"{self._mgmt_base_url()}/controller/createReplication"
        payload = {
            "fromBucket": source_bucket,
            "toCluster": remote_name,
            "toBucket": target_bucket,
            "replicationType": "continuous",
            "type": "xmem",
        }
        resp = requests.post(
            url, data=payload,
            auth=(self.source.username, self.source.password),
            verify=False, timeout=20,
        )
        if resp.status_code not in (200, 201):
            raise XDCRError(f"Failed to create replication for bucket {source_bucket}: {resp.text}")
        logger.info("XDCR replication started: %s -> %s/%s", source_bucket, remote_name, target_bucket)

    def _mgmt_base_url(self) -> str:
        host = (
            self.source.connection_string
            .replace("couchbases://", "").replace("couchbase://", "").split(",")[0]
        )
        scheme = "https" if self.source.use_tls else "http"
        port = 18091 if self.source.use_tls else 8091
        return f"{scheme}://{host}:{port}"

    def get_replication_status(self) -> list[dict]:
        """Raw XDCR task entries from /pools/default/tasks, one per bucket replication."""
        try:
            resp = requests.get(
                f"{self._mgmt_base_url()}/pools/default/tasks",
                auth=(self.source.username, self.source.password),
                verify=False, timeout=15,
            )
            resp.raise_for_status()
            return [t for t in resp.json() if t.get("type") == "xdcr"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch XDCR replication status: %s", exc)
            return []

    def get_aggregate_stats(self) -> dict:
        """Roll the per-bucket XDCR task entries up into the numbers the dashboard
        cares about: total changes left to replicate, docs written so far, and a
        rough throughput figure derived between polls by the caller."""
        tasks = self.get_replication_status()
        changes_left = sum(int(t.get("changesLeft", 0) or 0) for t in tasks)
        docs_written = sum(int(t.get("docsWritten", t.get("docsRepQueue", 0)) or 0) for t in tasks)
        errors = [e for t in tasks for e in (t.get("errors") or [])]
        active = any(t.get("status") == "running" for t in tasks) if tasks else False
        return {
            "active": active,
            "changes_left": changes_left,
            "docs_written": docs_written,
            "task_count": len(tasks),
            "errors": errors[:5],
        }

    def stop_replications(self, remote_name: str = REMOTE_CLUSTER_NAME) -> None:
        """Cancel every active XDCR task originating from this source and remove the
        remote-cluster reference, used both for cutover (replication no longer
        needed) and for a plain user-requested stop."""
        for task in self.get_replication_status():
            task_id = task.get("id") or task.get("cancelURI", "").rsplit("/", 1)[-1]
            if not task_id:
                continue
            try:
                resp = requests.post(
                    f"{self._mgmt_base_url()}/controller/cancelXDCR/{quote(task_id, safe='')}",
                    auth=(self.source.username, self.source.password),
                    verify=False, timeout=15,
                )
                if resp.status_code not in (200, 404):
                    logger.warning("Failed to cancel XDCR task %s: %s", task_id, resp.text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error cancelling XDCR task %s: %s", task_id, exc)

        try:
            resp = requests.delete(
                f"{self._mgmt_base_url()}/pools/default/remoteClusters/{quote(remote_name, safe='')}",
                auth=(self.source.username, self.source.password),
                verify=False, timeout=15,
            )
            if resp.status_code not in (200, 404):
                logger.warning("Failed to remove XDCR remote cluster reference: %s", resp.text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error removing XDCR remote cluster reference: %s", exc)

        logger.info("XDCR replication(s) from %s stopped.", self.source.label)
