"""Pydantic schemas shared across the API, vectorizer engine, and agent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator

from app.models.enums import JobPhase, OperationStatus, ValidationCheckId, ValidationSeverity


# ---------------------------------------------------------------------------
# Cluster connection configuration
# ---------------------------------------------------------------------------

class ClusterConnectionConfig(BaseModel):
    """User-supplied connection details for a source or destination cluster.
    Field set intentionally mirrors ClusterConnectionConfig from the Couchbase
    Migration Agent project so the two tools feel consistent to operators who
    use both."""
    label: str = Field(..., description="Friendly name shown in the UI")
    connection_string: str = Field(
        ..., description="couchbase:// or couchbases:// connection string, or Capella endpoint"
    )
    username: str
    password: str = Field(..., repr=False)
    is_capella: bool = False
    capella_cluster_id: Optional[str] = None
    capella_project_id: Optional[str] = None
    use_tls: bool = True
    ca_cert_path: Optional[str] = None

    # Vectorizer-specific addition: which bucket(s) hold the JSON documents (source)
    # or should receive the vectorized documents (destination). The wizard renders
    # this as one input per entry with a "+ Add bucket" button.
    bucket_names: list[str] = Field(default_factory=list, min_length=0)

    @field_validator("connection_string")
    @classmethod
    def _validate_scheme(cls, v: str) -> str:
        if not (v.startswith("couchbase://") or v.startswith("couchbases://") or v.startswith("https://")):
            raise ValueError(
                "connection_string must start with couchbase://, couchbases://, or https:// (Capella)"
            )
        return v


class BucketIntrospection(BaseModel):
    name: str
    item_count: Optional[int] = None
    ram_quota_mb: Optional[int] = None


class ClusterTopologySnapshot(BaseModel):
    """Introspected topology of a cluster, populated by the validator / test-connection."""
    cluster_uuid: Optional[str] = None
    cluster_version: Optional[str] = None
    is_enterprise: bool = False
    node_count: int = 0
    fts_service_available: bool = False
    buckets: list[BucketIntrospection] = Field(default_factory=list)
    supports_vector_search: bool = False


# ---------------------------------------------------------------------------
# Embedding models
# ---------------------------------------------------------------------------

class EmbeddingModelInfo(BaseModel):
    """One entry in the curated top-10-by-Hugging-Face-downloads embedding model
    registry (see core/embedding_models.py for the source list and citations)."""
    model_id: str                 # Hugging Face repo id, e.g. "BAAI/bge-m3"
    display_name: str
    dimensions: int
    popularity_rank: int
    approx_downloads: str
    description: str
    languages: str
    similarity_metric: str = "dot_product"  # dot_product | cosine | l2_norm
    text_prefix: Optional[str] = None       # e.g. "search_document: " for nomic/e5 families
    approx_size_mb: int = 0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationCheckResult(BaseModel):
    check_id: ValidationCheckId
    label: str
    severity: ValidationSeverity
    passed: bool
    message: str
    details: dict = Field(default_factory=dict)


class ValidationReport(BaseModel):
    job_id: UUID
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    checks: list[ValidationCheckResult] = Field(default_factory=list)
    source_topology: Optional[ClusterTopologySnapshot] = None
    dest_topology: Optional[ClusterTopologySnapshot] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == ValidationSeverity.ERROR)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_warnings(self) -> bool:
        return any(not c.passed and c.severity == ValidationSeverity.WARNING for c in self.checks)


# ---------------------------------------------------------------------------
# Job plan / stats / operations
# ---------------------------------------------------------------------------

class VectorizerJobCreate(BaseModel):
    name: str
    source: ClusterConnectionConfig
    destination: ClusterConnectionConfig
    same_server: bool = Field(
        True, description="If true, embeddings are written back into the source cluster/buckets in place."
    )
    embedding_model: str = Field(..., description="Hugging Face model id from /api/models")
    vector_field_name: str = "embedding"
    batch_size: int = Field(32, ge=1, le=256)
    poll_interval_seconds: float = Field(3.0, ge=0.5, le=120)


class OperationLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: OperationStatus
    bucket: Optional[str] = None
    doc_id: Optional[str] = None
    message: str


class VectorizerStats(BaseModel):
    server_connections: int = 0
    bucket_count: int = 0
    docs_total: int = 0
    docs_vectorized: int = 0
    docs_in_progress: int = 0
    docs_per_minute: float = 0.0
    per_bucket: dict[str, dict] = Field(default_factory=dict)
    last_updated: Optional[datetime] = None


class VectorizerJobRecord(BaseModel):
    job_id: UUID = Field(default_factory=uuid4)
    plan: VectorizerJobCreate
    phase: JobPhase = JobPhase.DRAFT
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    validation_report: Optional[ValidationReport] = None
    stats: VectorizerStats = Field(default_factory=VectorizerStats)
    recent_ops: list[OperationLogEntry] = Field(default_factory=list)
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent chat
# ---------------------------------------------------------------------------

class AgentChatRequest(BaseModel):
    job_id: Optional[UUID] = None
    message: str


class AgentChatResponse(BaseModel):
    reply: str
    suggested_actions: list[str] = Field(default_factory=list)
