"""Shared enumerations used across the migration agent."""
from enum import Enum


class ClusterTopologyType(str, Enum):
    SINGLE = "single_cluster"
    MULTI_CLUSTER = "multi_cluster"
    XDCR = "xdcr_replicated"


class MigrationStrategy(str, Enum):
    """How data moves from source to destination. Exposed to the user in the UI as
    a "replication mode" choice:
      - BACKUP_RESTORE -> "One-time migration": a single cbbackupmgr snapshot is
        taken and restored to the destination. The migration reaches COMPLETE once
        the restore finishes; nothing keeps syncing afterward.
      - XDCR_LIVE      -> "Continuous replication": XDCR is configured from source
        to destination and left running indefinitely. The migration sits in
        REPLICATING until the user explicitly stops it or performs a cutover.
      - HYBRID         -> "Bulk copy + continuous sync": a one-time backup/restore
        moves the existing data, then XDCR is started for ongoing delta sync,
        landing in REPLICATING the same as XDCR_LIVE.
    """
    BACKUP_RESTORE = "backup_restore"       # one-time: cbbackupmgr -> cbrestore
    XDCR_LIVE = "xdcr_live"                 # continuous: live XDCR replication
    HYBRID = "hybrid"                       # bulk backup/restore + continuous XDCR delta


class ReplicationMode(str, Enum):
    """User-facing grouping of MigrationStrategy into "is this ongoing or not".
    ONE_TIME <-> MigrationStrategy.BACKUP_RESTORE
    CONTINUOUS <-> MigrationStrategy.XDCR_LIVE or MigrationStrategy.HYBRID
    """
    ONE_TIME = "one_time"
    CONTINUOUS = "continuous"


CONTINUOUS_STRATEGIES = {MigrationStrategy.XDCR_LIVE, MigrationStrategy.HYBRID}


class MigrationPhase(str, Enum):
    DRAFT = "draft"
    VALIDATING = "validating"
    VALIDATED = "validated"
    VALIDATION_FAILED = "validation_failed"
    BACKUP_IN_PROGRESS = "backup_in_progress"
    BACKUP_COMPLETE = "backup_complete"
    BACKUP_FAILED = "backup_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    MIGRATING = "migrating"
    REPLICATING = "replicating"          # continuous XDCR sync is live and ongoing
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    STOPPED = "stopped"                  # continuous replication stopped without cutover
    CANCELLED = "cancelled"


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationCheckId(str, Enum):
    SOURCE_CONNECTIVITY = "source_connectivity"
    SOURCE_VERSION = "source_version"
    DEST_CONNECTIVITY = "dest_connectivity"
    DEST_CAPACITY = "dest_capacity"
    RBAC_PERMISSIONS = "rbac_permissions"
    BUCKET_SCHEMA_COMPAT = "bucket_schema_compat"
    INDEX_COMPAT = "index_compat"
    XDCR_TOPOLOGY = "xdcr_topology"
    DISK_SPACE = "disk_space"
    NETWORK_LATENCY = "network_latency"
    TLS_CONFIG = "tls_config"
    CROSS_VERSION_COMPAT = "cross_version_compat"


class BackupStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    # A brief transitional state: the agent has terminated a running backup on
    # purpose (not a failure) to relaunch it with a lower --threads value after
    # detecting sustained CPU saturation/thread oversubscription/memory pressure
    # on the source cluster. See bottleneck_detector.py and
    # MigrationEngine.backup_source()'s auto-throttle retry loop.
    THROTTLING = "throttling"
    COMPLETE = "complete"
    FAILED = "failed"
    RESTORED = "restored"


class NodeServiceType(str, Enum):
    DATA = "kv"
    INDEX = "index"
    QUERY = "n1ql"
    SEARCH = "fts"
    ANALYTICS = "cbas"
    EVENTING = "eventing"


class BottleneckKind(str, Enum):
    """Categories of backup/restore bottleneck the agent watches for while a
    migration is running, based on Couchbase's own guidance:
      - "Troubleshooting Slow Couchbase Backup and Restore Processes"
        (support.couchbase.com/hc/en-us/articles/24941535204763)
      - "Manage Backup Service Threads" thread-vs-CPU sizing formula
        (docs.couchbase.com/server/current/rest-api/backup-node-threads.html)
    See backend/app/core/bottleneck_detector.py for the detection logic itself.
    """
    THREAD_OVERSUBSCRIBED = "thread_oversubscribed"  # --threads above cpu_cores * 0.75
    CPU_SATURATED = "cpu_saturated"                   # a relevant node is near 100% CPU
    MEMORY_PRESSURE = "memory_pressure"               # a relevant node is low on free memory
    THROUGHPUT_STALLED = "throughput_stalled"         # ~0 MB/s for a sustained window
    THROUGHPUT_DEGRADED = "throughput_degraded"       # well below this run's own peak


MIN_SUPPORTED_VERSION = (7, 2, 0)
MAX_SUPPORTED_VERSION = (8, 0, 2)
