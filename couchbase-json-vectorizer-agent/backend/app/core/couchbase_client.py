"""
Thin wrapper around the Couchbase Python SDK (couchbase-python-client, cluster >= 4.x)
plus the cluster's REST management API (port 8091 / 18091, and FTS on 8094 / 18094),
used to:

  - introspect a cluster (version, edition, node count, FTS availability, buckets)
  - determine whether Couchbase Vector Search can run at all (Enterprise Edition or
    Capella, Couchbase Server 7.6.0+, FTS service enabled)
  - create the FTS vector index and a supporting partial GSI index used by the
    vectorizer engine's backfill/watch queries
  - read/write the actual JSON documents being vectorized

Works against on-prem clusters and Capella (Capella just uses couchbases:// with TLS
and Capella-issued credentials).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable

import requests
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.exceptions import CouchbaseException, DocumentNotFoundException
from couchbase.options import ClusterOptions

from app.models.enums import MIN_VECTOR_SEARCH_VERSION
from app.models.schemas import BucketIntrospection, ClusterConnectionConfig, ClusterTopologySnapshot

logger = logging.getLogger(__name__)


class CouchbaseClientError(RuntimeError):
    pass


class PendingDocQueryError(CouchbaseClientError):
    """Raised when the pending-document scan itself fails (e.g. no usable index
    on the bucket). Deliberately a distinct type from 'legitimately found zero
    pending documents', since the vectorizer engine's watch loop previously
    treated both cases identically -- a bucket with a broken query looked
    exactly like a bucket with nothing left to vectorize, and silently sat
    doing nothing forever."""


def _parse_version(version_str: str) -> tuple[int, int, int]:
    """'7.6.2-XXXX-enterprise' -> (7, 6, 2). Best-effort; unparsable -> (0, 0, 0)."""
    try:
        core = version_str.split("-")[0]
        parts = [int(p) for p in core.split(".")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


class CouchbaseClusterClient:
    """Wraps a live SDK connection plus the REST management API the SDK doesn't expose."""

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
        host = (
            self.config.connection_string.replace("couchbases://", "")
            .replace("couchbase://", "")
            .split(",")[0]
            .split("/")[0]
        )
        if self.config.is_capella:
            return f"https://{host}:18091"
        scheme = "https" if self.config.use_tls else "http"
        port = 18091 if self.config.use_tls else 8091
        return f"{scheme}://{host}:{port}"

    def _fts_base_url(self) -> str:
        return self._mgmt_base_url().replace(":18091", ":18094").replace(":8091", ":8094")

    def _rest_get(self, path: str, base: str | None = None) -> dict[str, Any]:
        url = f"{base or self._mgmt_base_url()}{path}"
        resp = requests.get(
            url,
            auth=(self.config.username, self.config.password),
            verify=self.config.ca_cert_path if self.config.ca_cert_path else False,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # -- introspection ----------------------------------------------------

    def get_pools_default(self) -> dict[str, Any]:
        return self._rest_get("/pools/default")

    def get_pools(self) -> dict[str, Any]:
        return self._rest_get("/pools")

    def snapshot_topology(self) -> ClusterTopologySnapshot:
        pools_default = self.get_pools_default()
        pools = self.get_pools()
        nodes = pools_default.get("nodes", [])
        version = nodes[0].get("version", "unknown") if nodes else "unknown"
        is_enterprise = any("enterprise" in (n.get("version") or "") for n in nodes) or bool(
            pools.get("isEnterprise")
        )

        all_services: set[str] = set()
        for n in nodes:
            all_services.update(n.get("services", []))
        fts_available = "fts" in all_services

        bucket_summaries = self._rest_get("/pools/default/buckets")
        buckets = [
            BucketIntrospection(
                name=b["name"],
                item_count=(b.get("basicStats") or {}).get("itemCount"),
                ram_quota_mb=(b.get("quota") or {}).get("rawRAM", 0) // (1024 * 1024) if b.get("quota") else None,
            )
            for b in bucket_summaries
        ]

        version_tuple = _parse_version(version)
        supports_vector_search = (
            is_enterprise or self.config.is_capella
        ) and fts_available and version_tuple >= MIN_VECTOR_SEARCH_VERSION

        return ClusterTopologySnapshot(
            cluster_uuid=pools_default.get("uuid") if isinstance(pools_default.get("uuid"), str) else None,
            cluster_version=version.split("-")[0],
            is_enterprise=is_enterprise or self.config.is_capella,
            node_count=len(nodes),
            fts_service_available=fts_available,
            buckets=buckets,
            supports_vector_search=supports_vector_search,
        )

    def bucket_item_count(self, bucket: str) -> int:
        cluster = self.connect()
        try:
            result = cluster.query(f"SELECT RAW COUNT(*) FROM `{bucket}`")
            rows = list(result)
            return int(rows[0]) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Item count query failed for bucket %s: %s", bucket, exc)
            return 0

    def bucket_vectorized_count(self, bucket: str, vector_field: str) -> int:
        cluster = self.connect()
        try:
            result = cluster.query(
                f"SELECT RAW COUNT(*) FROM `{bucket}` AS d WHERE d.`{vector_field}` IS NOT MISSING"
            )
            rows = list(result)
            return int(rows[0]) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vectorized count query failed for bucket %s: %s", bucket, exc)
            return 0

    # -- indexes ------------------------------------------------------------

    def ensure_pending_doc_index(self, bucket: str, vector_field: str) -> tuple[bool, str]:
        """Create a partial GSI index over documents still missing the vector field,
        so the vectorizer engine's backfill/watch scan (`WHERE field IS MISSING`)
        stays fast as buckets grow. Uses the same `AS d` alias as
        fetch_pending_documents()'s query below so the partial-index predicate is
        textually identical to the query predicate the engine actually runs --
        keeping them in sync avoids relying on the query planner to recognize two
        differently-written (if logically equivalent) expressions as the same
        thing."""
        index_name = f"idx_vectorizer_pending_{vector_field}"
        statement = (
            f"CREATE INDEX `{index_name}` ON `{bucket}` AS d (META(d).id) "
            f"WHERE d.`{vector_field}` IS MISSING"
        )
        cluster = self.connect()
        try:
            cluster.query(statement).execute()
            return True, f"Created pending-document index '{index_name}' on `{bucket}`."
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower() or "duplicate" in str(exc).lower():
                return True, f"Pending-document index '{index_name}' already exists on `{bucket}`."
            logger.warning("Could not create pending-doc index on %s: %s", bucket, exc)
            return False, f"Could not create pending-document index on `{bucket}`: {exc}"

    def ensure_primary_index(self, bucket: str) -> tuple[bool, str]:
        """Fallback safety net: guarantee *some* index exists on the bucket so
        every WHERE-filtered query the vectorizer engine runs (fetch_pending_documents,
        bucket_vectorized_count) has something to use, even if ensure_pending_doc_index()
        above failed for some cluster-specific reason (permissions, an existing
        conflicting index, etc). Without any index at all, those queries throw
        'No index available' -- which, before this fix, was being silently
        swallowed and misread as 'zero pending documents' instead of a real error."""
        statement = f"CREATE PRIMARY INDEX IF NOT EXISTS ON `{bucket}`"
        cluster = self.connect()
        try:
            cluster.query(statement).execute()
            return True, f"Primary index ready on `{bucket}`."
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not create primary index on %s: %s", bucket, exc)
            return False, f"Could not create primary index on `{bucket}`: {exc}"

    def ensure_vector_search_index(
        self, bucket: str, scope: str, collection: str, vector_field: str, dimensions: int, similarity: str
    ) -> tuple[bool, str]:
        """Create (or confirm) the Couchbase FTS vector index used by Vector Search
        queries against the newly-embedded documents. Requires Enterprise Edition or
        Capella -- Community Edition rejects vector-typed FTS fields outright, which
        is exactly why validator.py treats vector-search capability as a hard error
        rather than something to gracefully degrade around (unlike, e.g., the
        Couchbase Migration Agent's agent-memory store, which has a legitimate CE
        fallback for its own internal use)."""
        index_name = f"vidx_{bucket}_{collection}_{vector_field}"
        type_field_value = f"{scope}.{collection}" if scope != "_default" or collection != "_default" else "_default._default"
        definition = {
            "type": "fulltext-index",
            "name": index_name,
            "sourceType": "gocbcore",
            "sourceName": bucket,
            "planParams": {"maxPartitionsPerPIndex": 1024, "indexPartitions": 1},
            "params": {
                "doc_config": {
                    "mode": "scope.collection.type_field",
                    "docIdPrefixDelim": "",
                    "docIdRegexp": "",
                },
                "mapping": {
                    "default_mapping": {"enabled": False},
                    "index_dynamic": False,
                    "store_dynamic": False,
                    "types": {
                        type_field_value: {
                            "enabled": True,
                            "dynamic": False,
                            "properties": {
                                vector_field: {
                                    "enabled": True,
                                    "dynamic": False,
                                    "fields": [
                                        {
                                            "name": vector_field,
                                            "type": "vector",
                                            "dims": dimensions,
                                            "similarity": similarity,
                                            "vector_index_optimized_for": "recall",
                                            "index": True,
                                        }
                                    ],
                                }
                            },
                        }
                    },
                },
                "store": {"indexType": "scorch", "segmentVersion": 16},
            },
            "sourceParams": {},
        }

        fts = self._fts_base_url()
        existing = requests.get(
            f"{fts}/api/index/{index_name}",
            auth=(self.config.username, self.config.password),
            verify=False,
            timeout=10,
        )
        if existing.status_code == 200:
            return True, f"Vector search index '{index_name}' already exists."

        resp = requests.put(
            f"{fts}/api/index/{index_name}",
            auth=(self.config.username, self.config.password),
            json=definition,
            verify=False,
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 201):
            return True, f"Created Couchbase Vector Search index '{index_name}' ({dimensions} dims, {similarity})."
        if "vector typed fields not supported" in resp.text.lower():
            return False, (
                "This cluster rejected the vector index: vector-typed FTS fields require "
                "Couchbase Server Enterprise Edition (7.6.0+) or Capella. Community Edition "
                "cannot run Couchbase Vector Search."
            )
        return False, f"Vector search index creation failed ({resp.status_code}): {resp.text[:300]}"

    # -- document access ------------------------------------------------------

    def fetch_pending_documents(self, bucket: str, vector_field: str, limit: int) -> list[dict[str, Any]]:
        """Fetch up to `limit` documents in `bucket` that don't yet have `vector_field`,
        used for both the initial backfill and the continuous "watch for new documents"
        loop (new documents are, by definition, also missing the field until embedded).
        See vectorizer_engine.py for why polling rather than a raw DCP stream is used
        here -- the public Couchbase Python SDK doesn't expose a stable high-level DCP
        streaming API, and a bounded N1QL scan backed by the partial index created in
        ensure_pending_doc_index() is fast and fully supported."""
        cluster = self.connect()
        query = (
            f"SELECT META(d).id AS __doc_id, d.* FROM `{bucket}` AS d "
            f"WHERE d.`{vector_field}` IS MISSING LIMIT {int(limit)}"
        )
        try:
            result = cluster.query(query)
            return list(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Pending-document fetch failed for bucket %s: %s", bucket, exc)
            raise PendingDocQueryError(
                f"Query for documents pending vectorization in `{bucket}` failed: {exc}"
            ) from exc

    def upsert_document(self, bucket: str, doc_id: str, document: dict[str, Any]) -> None:
        cluster = self.connect()
        collection = cluster.bucket(bucket).default_collection()
        collection.upsert(doc_id, document)

    def get_document(self, bucket: str, doc_id: str) -> dict[str, Any] | None:
        cluster = self.connect()
        collection = cluster.bucket(bucket).default_collection()
        try:
            return collection.get(doc_id).content_as[dict]
        except DocumentNotFoundException:
            return None
