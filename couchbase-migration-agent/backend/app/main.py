"""FastAPI application entrypoint for the Couchbase Migration Agent."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agent, backup, clusters, migrations, stats
from app.config import get_settings
from app.websocket.progress import router as ws_router

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("migration_agent")

app = FastAPI(
    title=settings.app_name,
    description="Dockerized AI agent for migrating Couchbase Server clusters "
    "(7.2.0 - 8.0.2), including multi-cluster and XDCR topologies, to Couchbase Capella.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clusters.router, prefix="/api/clusters", tags=["clusters"])
app.include_router(migrations.router, prefix="/api/migrations", tags=["migrations"])
app.include_router(backup.router, prefix="/api/backup", tags=["backup"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(ws_router, tags=["websocket"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("%s starting up (env=%s)", settings.app_name, settings.environment)
