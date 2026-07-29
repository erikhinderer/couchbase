"""
Thin wrapper around the Couchbase Python SDK (couchbase-python-client, cluster >= 4.x)
used to introspect source / destination clusters: version, topology, buckets, scopes,
collections, indexes, and XDCR remotes.

Requires Couchbase Server 7.2.0 - 8.0.2. Works against on-prem clusters and Capella
(Capella just uses couchbases:// with TLS + Capella-issued credentials).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.exceptions import CouchbaseException
from couchbase.options import ClusterOptions
import requests

from app.models.enums import ClusterTopologyType
from app.models.schemas import ClusterConnectionConfig, ClusterNode, ClusterTopologySnapshot, XDCRRemote

logger = logging.getLogger(__name__)


class CouchbaseClientError(RuntimeError):
    pass


class CouchbaseClusterClient:
    """
    Wraps a live SDK connection plus the cluster's REST management API
    (port 8091 / 18091) which the SDK does not expose (pools/default, XDCR, etc).
    """

    def __init__(self, config: ClusterConnectionConfig):
        self.config = config
        self._cluster: Cluster | None = None

    # -- connection -----------------------------------------------------

    def connect(self, timeout_s: int = 15) -> Cluster:
        if self._cluster is not None:
            return self._cluster
        try:
            auth = PasswordAuthenticator(self.config.username, self.config.password)
            opts = ClusterOptions(auth)
            opts.apply_profile("wan_development")
            cluster = Cluster(self.config.connection_string, opts)
            cluster.wait_until_ready(timedelta(seconds=timeout_s))
            self._cluster = cluster
            return cluster
        except CouchbaseException as exc:
            raise CouchbaseClientError(f"Failed to connect to {self.config.label}: {exc}") from exc

    def close(self) -> None:
        if self._cluster is not None:
            self._cluster.close()
            self._cluster = None

    # -- REST management API helpers ------------------------------------

    def _mgmt_base_url(self) -> str:
        """Derive the cluster management REST base URL from the connection string."""
        host = (
            self.config.connection_string.replace("couchbases://", "")
            .replace("couchbase://", "")
            .split(",")[0]
            .split("/")[0]
        )
        if self.config.is_capella:
            # Capella exposes management REST on 18091 over TLS for provisioned clusters.
            return f"https://{host}:18091"
        scheme = "https" if self.config.use_tls else "http"
        port = 18091 if self.config.use_tls else 8091
        return f"{scheme}://{host}:{port}"

    def _rest_get(self, path: str) -> dict[str, Any]:
        url = f"{self._mgmt_base_url()}{path}"
        try:
            resp = requests.get(
                url,
                auth=(self.config.username, self.config.password),
                verify=self.config.ca_cert_path if self.config.ca_cert_path else False,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 401:
                raise CouchbaseClientError(
                    f"{self.config.label}: authentication failed (401 Unauthorized) calling "
                    f"{url}. The username/password were rejected by this cluster's management "
                    "REST API -- double check them (watch for trailing whitespace pasted into "
                    "the password field), and confirm that exact user exists on THIS cluster "
                    "with REST API access. This is unrelated to any session you may already be "
                    "logged into in the Couchbase Web Console -- REST calls always re-authenticate."
                ) from exc
            if status == 403:
                raise CouchbaseClientError(
                    f"{self.config.label}: {self.config.username!r} authenticated but lacks "
                    f"permission (403 Forbidden) for {url}. It needs a cluster-level admin role "
                    "(e.g. Full Admin or Read-Only Admin) -- a role scoped to just one bucket "
                    "(e.g. Data Reader/Writer) isn't enough for cluster introspection."
                ) from exc
            raise CouchbaseClientError(f"{self.config.label}: REST call to {url} failed: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise CouchbaseClientError(
                f"{self.config.label}: could not reach {url} ({exc}). Confirm the hostname/port "
                "are correct and reachable from inside the backend container, and that the "
                "TLS setting matches the cluster (couchbases:// / 'Use TLS' checked uses port "
                "18091; couchbase:// / unchecked uses 8091)."
            ) from exc
        return resp.json()

    # -- introspection ----------------------------------------------------

    def get_pools_default(self) -> dict[str, Any]:
        return self._rest_get("/pools/default")

    def get_server_version(self) -> str:
        pools = self.get_pools_default()
        nodes = pools.get("nodes", [])
        if not nodes:
            raise CouchbaseClientError("No nodes reported by cluster; cannot determine version")
        # version string looks like "7.6.2-XXXX-enterprise"
        return nodes[0].get("version", "unknown").split("-")[0]

    def get_nodes(self) -> list[ClusterNode]:
        pools = self.get_pools_default()
        nodes = []
        for n in pools.get("nodes", []):
            nodes.append(
                ClusterNode(
                    hostname=n.get("hostname", "unknown"),
                    services=n.get("services", ["kv"]),
                    version=n.get("version", "").split("-")[0],
                    status=n.get("status", "unknown"),
                )
            )
        return nodes

    def get_buckets(self) -> list[dict[str, Any]]:
        return self._rest_get("/pools/default/buckets")

    def get_scopes_and_collections(self, bucket: str) -> dict[str, list[str]]:
        data = self._rest_get(f"/pools/default/buckets/{bucket}/scopes")
        result: dict[str, list[str]] = {}
        for scope in data.get("scopes", []):
            result[scope["name"]] = [c["name"] for c in scope.get("collections", [])]
        return result

    def get_xdcr_remotes(self) -> list[XDCRRemote]:
        try:
            data = self._rest_get("/pools/default/remoteClusters")
        except CouchbaseClientError:
            # Non-fatal here: XDCR remotes are supplementary topology info, not
            # something that should block the whole introspection over (e.g.) a
            # user whose role can read buckets but not XDCR settings.
            return []
        remotes = []
        for r in data:
            replications = self._get_xdcr_replications_for_remote(r.get("name", ""))
            remotes.append(
                XDCRRemote(
                    name=r.get("name", "unknown"),
                    hostname=r.get("hostname", "unknown"),
                    uuid=r.get("uuid"),
                    replications=replications,
                )
            )
        return remotes

    def _get_xdcr_replications_for_remote(self, remote_name: str) -> list[str]:
        try:
            tasks = self._rest_get("/pools/default/tasks")
        except CouchbaseClientError:
            return []
        buckets = []
        for t in tasks:
            if t.get("type") == "xdcr" and t.get("target", "").split("/")[-2:-1] == [remote_name]:
                buckets.append(t.get("source", ""))
        return buckets

    def get_fts_indexes(self) -> list[str]:
        try:
            host = self._mgmt_base_url().replace(":18091", ":18094").replace(":8091", ":8094")
            resp = requests.get(
                f"{host}/api/index",
                auth=(self.config.username, self.config.password),
                verify=False,
                timeout=15,
            )
            resp.raise_for_status()
            return list(resp.json().get("indexDefs", {}).get("indexDefs", {}).keys())
        except Exception:  # noqa: BLE001 - FTS may not be provisioned; non-fatal
            return []

    def get_gsi_indexes(self, bucket: str) -> list[str]:
        try:
            cluster = self.connect()
            result = cluster.query(
                "SELECT name FROM system:indexes WHERE keyspace_id = $bucket",
                bucket=bucket,
            )
            return [row["name"] for row in result]
        except Exception:  # noqa: BLE001
            return []

    def snapshot_topology(self) -> ClusterTopologySnapshot:
        """Build a full topology snapshot used for validation and UI diagrams."""
        pools = self.get_pools_default()
        version = self.get_server_version()
        nodes = self.get_nodes()
        bucket_summaries = self.get_buckets()
        bucket_names = [b["name"] for b in bucket_summaries]

        scopes_by_bucket: dict[str, list[str]] = {}
        collections_by_bucket: dict[str, list[str]] = {}
        gsi_indexes: list[str] = []
        per_bucket_stats: dict[str, dict] = {}
        total_docs = 0
        total_size = 0

        for b in bucket_summaries:
            name = b["name"]
            try:
                scopes = self.get_scopes_and_collections(name)
                scopes_by_bucket[name] = list(scopes.keys())
                collections_by_bucket[name] = [c for cols in scopes.values() for c in cols]
            except Exception:  # noqa: BLE001
                scopes_by_bucket[name] = []
                collections_by_bucket[name] = []
            gsi_indexes.extend(self.get_gsi_indexes(name))
            stats = b.get("basicStats", {})
            item_count = stats.get("itemCount", 0) or 0
            data_size = stats.get("dataUsed", 0) or 0
            per_bucket_stats[name] = {"item_count": item_count, "data_size_bytes": data_size}
            total_docs += item_count
            total_size += data_size

        xdcr_remotes = self.get_xdcr_remotes()
        topology_type = ClusterTopologyType.SINGLE
        if len(nodes) > 1 and not xdcr_remotes:
            topology_type = ClusterTopologyType.MULTI_CLUSTER
        if xdcr_remotes:
            topology_type = ClusterTopologyType.XDCR

        return ClusterTopologySnapshot(
            topology_type=topology_type,
            cluster_uuid=pools.get("uuid") if isinstance(pools.get("uuid"), str) else None,
            cluster_version=version,
            nodes=nodes,
            buckets=bucket_names,
            scopes_by_bucket=scopes_by_bucket,
            collections_by_bucket=collections_by_bucket,
            total_docs=total_docs,
            total_data_size_bytes=total_size,
            xdcr_remotes=xdcr_remotes,
            fts_indexes=self.get_fts_indexes(),
            gsi_indexes=gsi_indexes,
            per_bucket_stats=per_bucket_stats,
        )
