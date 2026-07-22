"""FastAPI application entrypoint for the Couchbase JSON Vectorizer Agent."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agent, clusters, jobs, models, stats
from app.config import get_settings
from app.websocket.progress import router as ws_router

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("vectorizer_agent")

app = FastAPI(
    title=settings.app_name,
    description="Creates real-time vector embeddings for JSON documents in Couchbase, "
    "backfills existing documents, continuously vectorizes new ones, and validates that "
    "Couchbase Vector Search is fully operational against the result.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clusters.router, prefix="/api/clusters", tags=["clusters"])
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(ws_router, tags=["websocket"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("%s starting up (env=%s)", settings.app_name, settings.environment)
