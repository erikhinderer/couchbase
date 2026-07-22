"""Endpoints for validating and introspecting source/destination cluster connections."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.couchbase_client import CouchbaseClientError, CouchbaseClusterClient
from app.models.schemas import ClusterConnectionConfig, ClusterTopologySnapshot

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/test-connection", response_model=ClusterTopologySnapshot)
async def test_connection(config: ClusterConnectionConfig) -> ClusterTopologySnapshot:
    """Connect to a cluster and return its topology snapshot (version, edition, node
    count, FTS availability, buckets with item counts). Used by the wizard's
    'Test connection' button for both source and destination steps."""
    client = CouchbaseClusterClient(config)
    try:
        return client.snapshot_topology()
    except CouchbaseClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        client.close()
