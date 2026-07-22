"""
Runs the pre-launch validation checklist for a vectorizer job: connectivity to
source/destination, bucket access, and -- the core promise of this agent --
that Couchbase Vector Search is actually operable against the configured
buckets. Errors block launch; warnings don't.
"""
from __future__ import annotations

import logging
from uuid import UUID

from app.core.couchbase_client import CouchbaseClientError, CouchbaseClusterClient
from app.core.embedding_models import get_model_info
from app.core.embedding_service_client import EmbeddingServiceClient, EmbeddingServiceError
from app.models.enums import ValidationCheckId, ValidationSeverity
from app.models.schemas import (
    ClusterConnectionConfig,
    ValidationCheckResult,
    ValidationReport,
    VectorizerJobCreate,
)

logger = logging.getLogger(__name__)


async def validate_job(job_id: UUID, plan: VectorizerJobCreate) -> ValidationReport:
    checks: list[ValidationCheckResult] = []
    source_topo = None
    dest_topo = None

    # -- source connectivity + bucket access --------------------------------
    source_client = CouchbaseClusterClient(plan.source)
    try:
        source_topo = source_client.snapshot_topology()
        checks.append(ValidationCheckResult(
            check_id=ValidationCheckId.SOURCE_CONNECTIVITY,
            label="Source cluster connectivity",
            severity=ValidationSeverity.ERROR,
            passed=True,
            message=f"Connected to {plan.source.label} (Couchbase Server {source_topo.cluster_version}).",
        ))
        available = {b.name for b in source_topo.buckets}
        missing = [b for b in plan.source.bucket_names if b not in available]
        checks.append(ValidationCheckResult(
            check_id=ValidationCheckId.SOURCE_BUCKET_ACCESS,
            label="Source bucket access",
            severity=ValidationSeverity.ERROR,
            passed=not missing,
            message=(
                "All configured source buckets exist and are reachable."
                if not missing
                else f"Bucket(s) not found on source cluster: {', '.join(missing)}"
            ),
        ))
    except CouchbaseClientError as exc:
        checks.append(ValidationCheckResult(
            check_id=ValidationCheckId.SOURCE_CONNECTIVITY,
            label="Source cluster connectivity",
            severity=ValidationSeverity.ERROR,
            passed=False,
            message=str(exc),
        ))
    finally:
        source_client.close()

    # -- destination connectivity + bucket access ---------------------------
    if plan.same_server:
        dest_topo = source_topo
        checks.append(ValidationCheckResult(
            check_id=ValidationCheckId.DEST_CONNECTIVITY,
            label="Destination cluster connectivity",
            severity=ValidationSeverity.INFO,
            passed=True,
            message="Destination is the same server as the source; embeddings are written in place.",
        ))
    else:
        dest_client = CouchbaseClusterClient(plan.destination)
        try:
            dest_topo = dest_client.snapshot_topology()
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.DEST_CONNECTIVITY,
                label="Destination cluster connectivity",
                severity=ValidationSeverity.ERROR,
                passed=True,
                message=f"Connected to {plan.destination.label} (Couchbase Server {dest_topo.cluster_version}).",
            ))
            available = {b.name for b in dest_topo.buckets}
            missing = [b for b in plan.destination.bucket_names if b not in available]
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.DEST_BUCKET_ACCESS,
                label="Destination bucket access",
                severity=ValidationSeverity.ERROR,
                passed=not missing,
                message=(
                    "All configured destination buckets exist and are reachable."
                    if not missing
                    else f"Bucket(s) not found on destination cluster: {', '.join(missing)}"
                ),
            ))
        except CouchbaseClientError as exc:
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.DEST_CONNECTIVITY,
                label="Destination cluster connectivity",
                severity=ValidationSeverity.ERROR,
                passed=False,
                message=str(exc),
            ))
        finally:
            dest_client.close()

    # -- vector search capability --------------------------------------------
    target_topo = dest_topo or source_topo
    supports_vs = bool(target_topo and target_topo.supports_vector_search)
    checks.append(ValidationCheckResult(
        check_id=ValidationCheckId.VECTOR_SEARCH_CAPABILITY,
        label="Couchbase Vector Search capability",
        severity=ValidationSeverity.ERROR,
        passed=supports_vs,
        message=(
            "Cluster supports Couchbase Vector Search (Enterprise Edition/Capella, "
            "7.6.0+, FTS service enabled)."
            if supports_vs
            else "Vector Search requires Couchbase Server Enterprise Edition or Capella, "
            "version 7.6.0 or later, with the Search (FTS) service enabled. This cluster "
            "does not meet that bar."
        ),
    ))

    # -- embedding service health + model --------------------------------
    model_info = get_model_info(plan.embedding_model)
    if model_info is None:
        checks.append(ValidationCheckResult(
            check_id=ValidationCheckId.EMBEDDING_MODEL_LOADED,
            label="Embedding model selection",
            severity=ValidationSeverity.ERROR,
            passed=False,
            message=f"Unknown embedding model '{plan.embedding_model}'.",
        ))
    else:
        client = EmbeddingServiceClient()
        try:
            await client.health()
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.EMBEDDING_SERVICE_HEALTH,
                label="Embedding service reachable",
                severity=ValidationSeverity.ERROR,
                passed=True,
                message="embedding-service is up.",
            ))
            await client.warmup(model_info.model_id)
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.EMBEDDING_MODEL_LOADED,
                label="Embedding model loaded",
                severity=ValidationSeverity.ERROR,
                passed=True,
                message=f"{model_info.display_name} ({model_info.dimensions} dims) loaded and ready.",
            ))
        except EmbeddingServiceError as exc:
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.EMBEDDING_MODEL_LOADED,
                label="Embedding model loaded",
                severity=ValidationSeverity.ERROR,
                passed=False,
                message=str(exc),
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(ValidationCheckResult(
                check_id=ValidationCheckId.EMBEDDING_SERVICE_HEALTH,
                label="Embedding service reachable",
                severity=ValidationSeverity.ERROR,
                passed=False,
                message=f"embedding-service unreachable: {exc}",
            ))

    # -- vector index + pending-doc index creation (only if everything else passed) --
    if supports_vs and model_info is not None and all(
        c.passed for c in checks if c.severity == ValidationSeverity.ERROR
    ):
        target_config: ClusterConnectionConfig = plan.destination if not plan.same_server else plan.source
        target_client = CouchbaseClusterClient(target_config)
        try:
            for bucket in (target_config.bucket_names or plan.source.bucket_names):
                ok, msg = target_client.ensure_vector_search_index(
                    bucket=bucket,
                    scope="_default",
                    collection="_default",
                    vector_field=plan.vector_field_name,
                    dimensions=model_info.dimensions,
                    similarity=model_info.similarity_metric,
                )
                checks.append(ValidationCheckResult(
                    check_id=ValidationCheckId.VECTOR_INDEX_READY,
                    label=f"Vector search index on `{bucket}`",
                    severity=ValidationSeverity.ERROR,
                    passed=ok,
                    message=msg,
                ))
            for bucket in plan.source.bucket_names:
                ok, msg = CouchbaseClusterClient(plan.source).ensure_pending_doc_index(
                    bucket, plan.vector_field_name
                )
                checks.append(ValidationCheckResult(
                    check_id=ValidationCheckId.PENDING_DOC_INDEX,
                    label=f"Pending-document index on `{bucket}`",
                    severity=ValidationSeverity.WARNING,
                    passed=ok,
                    message=msg,
                ))
        finally:
            target_client.close()

    return ValidationReport(
        job_id=job_id,
        checks=checks,
        source_topology=source_topo,
        dest_topology=dest_topo,
    )
