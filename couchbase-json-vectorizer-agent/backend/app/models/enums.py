"""Shared enumerations used across the JSON Vectorizer Agent."""
from enum import Enum


class JobPhase(str, Enum):
    """Lifecycle of a vectorizer job, shown as a badge in the dashboard/wizard."""
    DRAFT = "draft"
    VALIDATING = "validating"
    VALIDATION_FAILED = "validation_failed"
    READY = "ready"                # validated, not yet launched
    BACKFILLING = "backfilling"    # actively embedding a backlog of existing docs
    WATCHING = "watching"          # backlog drained; polling for newly-created docs
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationCheckId(str, Enum):
    SOURCE_CONNECTIVITY = "source_connectivity"
    SOURCE_BUCKET_ACCESS = "source_bucket_access"
    DEST_CONNECTIVITY = "dest_connectivity"
    DEST_BUCKET_ACCESS = "dest_bucket_access"
    VECTOR_SEARCH_CAPABILITY = "vector_search_capability"
    RBAC_PERMISSIONS = "rbac_permissions"
    TLS_CONFIG = "tls_config"
    EMBEDDING_SERVICE_HEALTH = "embedding_service_health"
    EMBEDDING_MODEL_LOADED = "embedding_model_loaded"
    VECTOR_INDEX_READY = "vector_index_ready"
    PENDING_DOC_INDEX = "pending_doc_index"


class OperationStatus(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


MIN_VECTOR_SEARCH_VERSION = (7, 6, 0)
