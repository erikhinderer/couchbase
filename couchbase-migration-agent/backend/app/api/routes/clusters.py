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
    """Connect to a cluster and return its topology snapshot. Used by the wizard's
    'Validate' button for both source and destination before a migration is created."""
    client = CouchbaseClusterClient(config)
    try:
        return client.snapshot_topology()
    except CouchbaseClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        client.close()
