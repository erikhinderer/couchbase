"""Read-only endpoints for dashboard statistics (used on initial load; live updates
stream over the /ws/jobs websocket)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.store import JobStore
from app.models.schemas import VectorizerStats

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard_stats() -> dict:
    """Aggregate stats across every job, for the dashboard's top-line stat cards."""
    jobs = await JobStore.instance().list_all()
    agg = VectorizerStats()
    recent_ops = []
    for job in jobs:
        agg.server_connections = max(agg.server_connections, job.stats.server_connections)
        agg.bucket_count += job.stats.bucket_count
        agg.docs_total += job.stats.docs_total
        agg.docs_vectorized += job.stats.docs_vectorized
        agg.docs_in_progress += job.stats.docs_in_progress
        agg.docs_per_minute += job.stats.docs_per_minute
        recent_ops.extend(job.recent_ops)
    recent_ops.sort(key=lambda o: o.timestamp, reverse=True)
    return {
        "stats": agg,
        "recent_ops": recent_ops[:100],
        "jobs": jobs,
    }


@router.get("/{job_id}", response_model=VectorizerStats)
async def get_job_stats(job_id: UUID):
    record = await JobStore.instance().get(job_id)
    if not record:
        raise HTTPException(404, "Job not found")
    return record.stats
