"""Pydantic schemas shared across the API, migration engine, and agent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator

from app.models.enums import (
    BackupStatus,
    ClusterTopologyType,
    MigrationPhase,
    MigrationStrategy,
    ValidationCheckId,
    ValidationSeverity,
)


# ---------------------------------------------------------------------------
# Cluster connection configuration
# ---------------------------------------------------------------------------

class ClusterNode(BaseModel):
    hostname: str
    services: list[str] = Field(default_factory=lambda: ["kv"])
    version: Optional[str] = None
    status: Optional[str] = "healthy"


class XDCRRemote(BaseModel):
    """A remote cluster reference configured for XDCR replication."""
    name: str
    hostname: str
    uuid: Optional[str] = None
    replications: list[str] = Field(default_factory=list)  # bucket names being replicated


class ClusterConnectionConfig(BaseModel):
    """User-supplied connection details for a source or destination cluster."""
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
    use_external_network: bool = Field(
        False,
        description=(
            "For clusters that advertise a different internal-vs-external address per node "
            "(common on AWS/GCP/Azure VMs and Kubernetes/CAO) -- appends ?network=external to "
            "the -c/--cluster argument passed to cbbackupmgr so it resolves each node's "
            "alternate/external address instead of its internal one. Without this, backup/"
            "restore can connect fine for the initial handshake (which uses the hostname you "
            "typed) but then fail partway through with 'connection refused' once it needs to "
            "reach a specific node/service (commonly while transferring GSI index definitions) "
            "using the internal address from the cluster map. Also requires the cluster's "
            "external/alternate addresses to actually be configured server-side (Couchbase Web "
            "Console: Server Nodes -> node -> External IP Address), and the corresponding ports "
            "reachable from wherever this backend container runs."
        ),
    )

    @field_validator("connection_string")
    @classmethod
    def _validate_scheme(cls, v: str) -> str:
        if not (v.startswith("couchbase://") or v.startswith("couchbases://") or v.startswith("https://")):
            raise ValueError(
                "connection_string must start with couchbase://, couchbases://, or https:// (Capella)"
            )
        return v


class ClusterTopologySnapshot(BaseModel):
    """Introspected topology of a cluster, populated by the validator."""
    topology_type: ClusterTopologyType = ClusterTopologyType.SINGLE
    cluster_uuid: Optional[str] = None
    cluster_version: Optional[str] = None
    nodes: list[ClusterNode] = Field(default_factory=list)
    buckets: list[str] = Field(default_factory=list)
    scopes_by_bucket: dict[str, list[str]] = Field(default_factory=dict)
    collections_by_bucket: dict[str, list[str]] = Field(default_factory=dict)
    total_docs: Optional[int] = None
    total_data_size_bytes: Optional[int] = None
    xdcr_remotes: list[XDCRRemote] = Field(default_factory=list)
    fts_indexes: list[str] = Field(default_factory=list)
    gsi_indexes: list[str] = Field(default_factory=list)


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
    migration_id: UUID
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    checks: list[ValidationCheckResult] = Field(default_factory=list)
    source_topology: Optional[ClusterTopologySnapshot] = None
    dest_topology: Optional[ClusterTopologySnapshot] = None

    # `passed` / `has_warnings` must be @computed_field, not a plain @property --
    # pydantic v2 only serializes plain @property methods on request (they're
    # excluded from .model_dump()/JSON by default), so the frontend was seeing
    # `validation.passed === undefined` and treating every validation as failed
    # regardless of the individual check results.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == ValidationSeverity.ERROR)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_warnings(self) -> bool:
        return any(not c.passed and c.severity == ValidationSeverity.WARNING for c in self.checks)


# ---------------------------------------------------------------------------
# Backup / rollback
# ---------------------------------------------------------------------------

class BackupRecord(BaseModel):
    backup_id: UUID = Field(default_factory=uuid4)
    migration_id: UUID
    status: BackupStatus = BackupStatus.PENDING
    archive_path: str
    repo_name: str
    buckets: list[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    size_bytes: Optional[int] = None
    error_message: Optional[str] = None


class RollbackRequest(BaseModel):
    migration_id: UUID
    reason: str = "user_requested"
    restore_source: bool = True


class ReplicationStopRequest(BaseModel):
    """Stops a continuous (XDCR_LIVE / HYBRID) replication that is currently in the
    REPLICATING phase. `perform_cutover=True` tears down XDCR and marks the migration
    COMPLETE (destination is now the system of record); `perform_cutover=False` tears
    down XDCR and marks it STOPPED without treating the migration as finished."""
    migration_id: UUID
    perform_cutover: bool = True
    restore_source: bool = True


# ---------------------------------------------------------------------------
# Migration plan / status
# ---------------------------------------------------------------------------

class BucketMigrationSpec(BaseModel):
    bucket_name: str
    include: bool = True
    target_bucket_name: Optional[str] = None
    ram_quota_mb: Optional[int] = None
    migrate_indexes: bool = True
    migrate_fts_indexes: bool = True


class MigrationPlanCreate(BaseModel):
    name: str
    source: ClusterConnectionConfig
    destination: ClusterConnectionConfig
    strategy: MigrationStrategy = MigrationStrategy.BACKUP_RESTORE
    buckets: list[BucketMigrationSpec] = Field(default_factory=list)
    include_xdcr_remotes: bool = True
    parallelism: int = Field(4, ge=1, le=32)
    throttle_mb_per_sec: Optional[int] = None
    cutover_window_minutes: Optional[int] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_continuous(self) -> bool:
        """True for replication modes that stay running (XDCR_LIVE / HYBRID) rather
        than completing after a single snapshot (BACKUP_RESTORE)."""
        return self.strategy in (MigrationStrategy.XDCR_LIVE, MigrationStrategy.HYBRID)


class MigrationStats(BaseModel):
    docs_total: int = 0
    docs_migrated: int = 0
    docs_failed: int = 0
    bytes_total: int = 0
    bytes_migrated: int = 0
    throughput_docs_per_sec: float = 0.0
    throughput_mb_per_sec: float = 0.0
    avg_latency_ms: float = 0.0
    error_rate_pct: float = 0.0
    elapsed_seconds: float = 0.0
    eta_seconds: Optional[float] = None
    per_bucket: dict[str, dict] = Field(default_factory=dict)

    # -- continuous replication (XDCR_LIVE / HYBRID) only --
    replication_active: bool = False
    changes_left: Optional[int] = None
    mutations_replicated: int = 0
    mutations_per_sec: float = 0.0
    replication_lag_seconds: Optional[float] = None
    last_replication_poll: Optional[datetime] = None


class MigrationRecord(BaseModel):
    migration_id: UUID = Field(default_factory=uuid4)
    plan: MigrationPlanCreate
    phase: MigrationPhase = MigrationPhase.DRAFT
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    validation_report: Optional[ValidationReport] = None
    backup_record: Optional[BackupRecord] = None
    stats: MigrationStats = Field(default_factory=MigrationStats)
    log_tail: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class MigrationApproval(BaseModel):
    migration_id: UUID
    approved_by: str
    confirm_backup_verified: bool = True


# ---------------------------------------------------------------------------
# Agent chat / memory
# ---------------------------------------------------------------------------

class AgentChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str
    migration_id: Optional[UUID] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentChatRequest(BaseModel):
    migration_id: Optional[UUID] = None
    message: str
    use_memory: bool = True


class AgentChatResponse(BaseModel):
    reply: str
    recalled_memories: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
