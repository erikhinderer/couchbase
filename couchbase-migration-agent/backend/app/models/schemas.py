"""Pydantic schemas shared across the API, migration engine, and agent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator

from app.models.enums import (
    BackupStatus,
    BottleneckKind,
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
    # Live progress, parsed from cbbackupmgr's own progress-bar output while the
    # backup subprocess is running (see BackupManager._run_streaming()). progress_pct
    # comes directly from cbbackupmgr's own percentage (it accounts for bucket
    # config/GSI/FTS/KV phases, not just raw item count, so it's more accurate than
    # anything we'd derive ourselves from docs_done alone). All fields stay at their
    # defaults for a backup that hasn't started streaming yet.
    progress_pct: float = 0.0
    docs_done: int = 0
    size_mb_done: float = 0.0
    throughput_mb_per_sec: float = 0.0
    eta_seconds: Optional[float] = None
    elapsed_seconds: float = 0.0


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


class BottleneckFinding(BaseModel):
    """A single detected backup/restore bottleneck, produced by BottleneckMonitor
    (backend/app/core/bottleneck_detector.py) while a migration is actively running.
    For the BACKUP phase, a thread-actionable finding (CPU saturation, thread
    oversubscription, memory pressure) is handled automatically -- the agent stops
    and relaunches the backup at a lower --threads value (see
    MigrationEngine.backup_source()'s auto-throttle retry loop); auto_remediated is
    True on the follow-up finding that reports what it did. Everything else --
    restore-phase findings (the destination side isn't under this app's process
    control the way the backup subprocess it launched is) and throughput
    stall/degraded findings on either phase (a threads change doesn't fix a network
    problem) -- stays diagnosis + suggestion only, same as before. Surfaced to the
    user via the websocket and proactively in the Ask The Agent panel."""
    finding_id: UUID = Field(default_factory=uuid4)
    kind: BottleneckKind
    phase: str  # "backup" or "restore" -- which leg of the pipeline this concerns
    cluster_label: str  # which cluster (source/destination label) this concerns
    message: str  # what was observed, with the metric(s) that triggered it
    suggestion: str  # concrete, actionable remediation text (or, if auto_remediated, what was just done)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    # Only meaningful for thread-actionable kinds (CPU_SATURATED/THREAD_OVERSUBSCRIBED/
    # MEMORY_PRESSURE): the --threads value Couchbase's own backup-service sizing
    # formula (max(1, cpu_cores * 0.75)) recommends for the busiest node observed.
    recommended_threads: Optional[int] = None
    # True only on the follow-up finding posted once the agent has actually stopped
    # and relaunched a backup at a lower thread count -- never set on the initial
    # detection finding, and never set for restore-phase or stall/degraded findings,
    # which the agent can't and doesn't act on by itself.
    auto_remediated: bool = False


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
    bottleneck_findings: list[BottleneckFinding] = Field(default_factory=list)


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
