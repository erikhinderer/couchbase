"""
The core vectorizer loop: for every configured source bucket, repeatedly (a)
backfills existing JSON documents that don't yet have a vector embedding, then
(b) keeps polling for newly-created documents (which are, by definition, also
missing the field) and embeds those too -- satisfying both "vectorize existing
documents" and "continuously vectorize new documents" from a single code path.

Why polling instead of a DCP change feed: Couchbase's Database Change Protocol
(DCP) is how tools like XDCR and cbbackupmgr get a true low-level mutation
stream, but the public `couchbase` Python SDK doesn't expose a stable
high-level DCP streaming API for application code (the official pattern for
"react to mutations" from an application is either Couchbase Eventing Service
functions running inside the cluster, or an external DCP client library in
Go/Java/C). Polling with `WHERE <vector_field> IS MISSING`, backed by the
partial GSI index created in couchbase_client.ensure_pending_doc_index(), is
fully supported by the standard SDK, requires no extra cluster-side services,
and -- because the poll interval is a few seconds -- is effectively real-time
from a human's perspective while staying simple enough to be reliable. If you
need true sub-second reaction to writes, front this with a Couchbase Eventing
function that calls this agent's API, or replace this module with a DCP client.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from uuid import UUID

from app.core.couchbase_client import CouchbaseClusterClient
from app.core.embedding_models import get_model_info
from app.core.embedding_service_client import EmbeddingServiceClient
from app.core.store import JobStore
from app.models.enums import JobPhase, OperationStatus
from app.models.schemas import OperationLogEntry, VectorizerJobRecord

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[VectorizerJobRecord], Awaitable[None]]


def extract_embeddable_text(doc: dict, max_chars: int) -> str:
    """Flatten every string leaf value in an arbitrary JSON document into one text
    blob for embedding. Deliberately generic (works on any document shape without
    per-collection configuration) rather than picking specific fields -- documents
    vary too much across buckets for a fixed field list to be safe as a default.
    Non-string scalars are included as their string form; the special `__doc_id`
    projection field added by fetch_pending_documents() is skipped."""
    parts: list[str] = []

    def walk(value, depth: int = 0):
        if depth > 6:
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            parts.append(str(value))
        elif isinstance(value, dict):
            for k, v in value.items():
                if k == "__doc_id":
                    continue
                walk(v, depth + 1)
        elif isinstance(value, list):
            for item in value[:50]:
                walk(item, depth + 1)

    walk(doc)
    text = " ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text


class _JobRuntime:
    """In-memory (non-persisted) runtime state for one active job: the asyncio
    task driving it and a rolling window of recent vectorization timestamps used
    to compute docs/minute."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.completions: deque[datetime] = deque(maxlen=5000)
        self.cancelled = False


class VectorizerEngine:
    """Singleton owning one background asyncio task per active job."""

    _instance: "VectorizerEngine | None" = None

    def __init__(self) -> None:
        self._runtimes: dict[str, _JobRuntime] = {}

    @classmethod
    def instance(cls) -> "VectorizerEngine":
        if cls._instance is None:
            cls._instance = VectorizerEngine()
        return cls._instance

    def is_running(self, job_id: UUID) -> bool:
        rt = self._runtimes.get(str(job_id))
        return bool(rt and rt.task and not rt.task.done())

    async def start(self, job_id: UUID, on_progress: ProgressCallback) -> None:
        if self.is_running(job_id):
            return
        rt = _JobRuntime()
        self._runtimes[str(job_id)] = rt
        rt.task = asyncio.create_task(self._run(job_id, rt, on_progress))

    async def stop(self, job_id: UUID) -> None:
        rt = self._runtimes.get(str(job_id))
        if not rt:
            return
        rt.cancelled = True
        if rt.task:
            rt.task.cancel()
            try:
                await rt.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        record = await JobStore.instance().get(job_id)
        if record:
            record.phase = JobPhase.STOPPED
            record.updated_at = datetime.utcnow()
            await JobStore.instance().save(record)

    def _log(self, record: VectorizerJobRecord, status: OperationStatus, message: str, bucket: str | None = None, doc_id: str | None = None) -> None:
        record.recent_ops.insert(0, OperationLogEntry(status=status, bucket=bucket, doc_id=doc_id, message=message))
        max_entries = 500
        if len(record.recent_ops) > max_entries:
            del record.recent_ops[max_entries:]

    async def _run(self, job_id: UUID, rt: _JobRuntime, on_progress: ProgressCallback) -> None:
        store = JobStore.instance()
        record = await store.get(job_id)
        if not record:
            logger.error("VectorizerEngine: job %s not found at start", job_id)
            return

        plan = record.plan
        model_info = get_model_info(plan.embedding_model)
        if model_info is None:
            record.phase = JobPhase.FAILED
            record.error_message = f"Unknown embedding model {plan.embedding_model}"
            await store.save(record)
            await on_progress(record)
            return

        embed_client = EmbeddingServiceClient()
        source_client = CouchbaseClusterClient(plan.source)
        dest_client = source_client if plan.same_server else CouchbaseClusterClient(plan.destination)

        source_buckets = plan.source.bucket_names
        dest_buckets = plan.destination.bucket_names if not plan.same_server else plan.source.bucket_names

        def dest_bucket_for(i: int, source_bucket: str) -> str:
            # 1:1 index mapping when destination lists an equal number of buckets;
            # otherwise assume the same bucket name exists on the destination too
            # (validated up front for the same_server=False case).
            if i < len(dest_buckets):
                return dest_buckets[i]
            return source_bucket

        try:
            while not rt.cancelled:
                any_pending = False
                for i, bucket in enumerate(source_buckets):
                    if rt.cancelled:
                        break
                    pending = source_client.fetch_pending_documents(
                        bucket, plan.vector_field_name, plan.batch_size
                    )
                    if not pending:
                        continue

                    any_pending = True
                    record.phase = JobPhase.BACKFILLING
                    record.stats.docs_in_progress = len(pending)
                    await store.save(record)
                    await on_progress(record)

                    doc_ids = [d["__doc_id"] for d in pending]
                    texts = [extract_embeddable_text(d, 8000) for d in pending]

                    try:
                        vectors = await embed_client.embed(
                            model_info.model_id, texts, text_prefix=model_info.text_prefix
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._log(record, OperationStatus.ERROR, f"Embedding batch failed: {exc}", bucket=bucket)
                        record.stats.docs_in_progress = 0
                        await store.save(record)
                        await on_progress(record)
                        await asyncio.sleep(plan.poll_interval_seconds)
                        continue

                    target_bucket = dest_bucket_for(i, bucket)
                    for doc, doc_id, vector in zip(pending, doc_ids, vectors):
                        clean_doc = {k: v for k, v in doc.items() if k != "__doc_id"}
                        clean_doc[plan.vector_field_name] = vector
                        clean_doc["_vectorized_at"] = datetime.utcnow().isoformat()
                        clean_doc["_vectorizer_model"] = model_info.model_id
                        try:
                            dest_client.upsert_document(target_bucket, doc_id, clean_doc)
                            rt.completions.append(datetime.utcnow())
                            self._log(
                                record, OperationStatus.SUCCESS,
                                f"Vectorized document with {model_info.display_name} "
                                f"({model_info.dimensions} dims) -> `{target_bucket}`",
                                bucket=bucket, doc_id=doc_id,
                            )
                        except Exception as exc:  # noqa: BLE001
                            self._log(record, OperationStatus.ERROR, f"Failed to write embedding: {exc}", bucket=bucket, doc_id=doc_id)

                    record.stats.docs_vectorized += len(pending)
                    record.stats.docs_in_progress = 0
                    self._refresh_rate(record, rt)
                    await self._refresh_totals(record, source_client, plan)
                    record.updated_at = datetime.utcnow()
                    await store.save(record)
                    await on_progress(record)

                if not any_pending:
                    record.phase = JobPhase.WATCHING
                    record.stats.docs_in_progress = 0
                    self._refresh_rate(record, rt)
                    await self._refresh_totals(record, source_client, plan)
                    await store.save(record)
                    await on_progress(record)
                    await asyncio.sleep(plan.poll_interval_seconds)
                # else: loop again immediately to keep draining the backlog quickly

        except asyncio.CancelledError:
            pass
        finally:
            source_client.close()
            if dest_client is not source_client:
                dest_client.close()

    def _refresh_rate(self, record: VectorizerJobRecord, rt: _JobRuntime) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=1)
        while rt.completions and rt.completions[0] < cutoff:
            rt.completions.popleft()
        record.stats.docs_per_minute = float(len(rt.completions))
        record.stats.last_updated = datetime.utcnow()

    async def _refresh_totals(self, record: VectorizerJobRecord, source_client: CouchbaseClusterClient, plan) -> None:
        total = 0
        vectorized = 0
        per_bucket: dict[str, dict] = {}
        for bucket in plan.source.bucket_names:
            t = source_client.bucket_item_count(bucket)
            v = source_client.bucket_vectorized_count(bucket, plan.vector_field_name)
            total += t
            vectorized += v
            per_bucket[bucket] = {"docs_total": t, "docs_vectorized": v}
        record.stats.docs_total = total
        record.stats.docs_vectorized = vectorized
        record.stats.bucket_count = len(plan.source.bucket_names) + (
            0 if plan.same_server else len(plan.destination.bucket_names)
        )
        record.stats.server_connections = 1 if plan.same_server else 2
        record.stats.per_bucket = per_bucket
