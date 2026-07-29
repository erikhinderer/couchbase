"""FastAPI application entrypoint for the Couchbase Onboarding Agent."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agent, migrations, sources, stats
from app.config import get_settings
from app.websocket.progress import router as ws_router

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("onboarding_agent")

app = FastAPI(
    title=settings.app_name,
    description="Dockerized AI agent for migrating MongoDB, Amazon DynamoDB, Redis, Apache "
    "Cassandra, and Microsoft Azure Cosmos DB into Couchbase Server (Enterprise Edition) or "
    "Couchbase Capella.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources.router, prefix="/api/sources", tags=["sources"])
app.include_router(migrations.router, prefix="/api/migrations", tags=["migrations"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(ws_router, tags=["websocket"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/source-types")
async def source_types() -> list[dict]:
    """Static metadata the wizard uses to render the source-type picker and to
    decide which connection-form fields to show for each of the five source
    databases."""
    return [
        {"value": "mongodb", "label": "MongoDB", "fields": [
            "connection_string", "database", "username", "password", "use_tls",
        ]},
        {"value": "dynamodb", "label": "Amazon DynamoDB", "fields": [
            "aws_region", "aws_access_key_id", "aws_secret_access_key", "aws_session_token",
            "dynamodb_endpoint_url",
        ]},
        {"value": "redis", "label": "Redis", "fields": [
            "connection_string", "redis_db_index", "username", "password", "use_tls",
        ]},
        {"value": "cassandra", "label": "Apache Cassandra", "fields": [
            "connection_string", "cassandra_port", "cassandra_datacenter", "database",
            "username", "password", "use_tls",
        ]},
        {"value": "cosmosdb", "label": "Microsoft Azure Cosmos DB", "fields": [
            "cosmos_endpoint", "cosmos_key", "database",
        ]},
    ]


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("%s starting up (env=%s)", settings.app_name, settings.environment)
