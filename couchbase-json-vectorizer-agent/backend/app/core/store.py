"""
Lightweight persistence for vectorizer job records. Deliberately simple (JSON
file on a mounted volume), matching the pattern used by the Couchbase Migration
Agent's MigrationStore -- no hard dependency on an additional database. Swap
this for a Couchbase collection or Postgres table if you need multiple backend
replicas.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.models.schemas import VectorizerJobRecord

logger = logging.getLogger(__name__)


class JobStore:
    _instance: "JobStore | None" = None

    def __init__(self) -> None:
        self.settings = get_settings()
        self._lock = asyncio.Lock()
        self._records: dict[str, VectorizerJobRecord] = {}
        self._load()

    @classmethod
    def instance(cls) -> "JobStore":
        if cls._instance is None:
            cls._instance = JobStore()
        return cls._instance

    def _path(self) -> Path:
        return Path(self.settings.vectorizer_state_file)

    def _load(self) -> None:
        p = self._path()
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text())
            for jid, data in raw.items():
                self._records[jid] = VectorizerJobRecord.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load job state file %s: %s", p, exc)

    def _persist(self) -> None:
        p = self._path()
        os.makedirs(p.parent, exist_ok=True)
        payload = {jid: json.loads(r.model_dump_json()) for jid, r in self._records.items()}
        p.write_text(json.dumps(payload, indent=2, default=str))

    async def save(self, record: VectorizerJobRecord) -> None:
        async with self._lock:
            self._records[str(record.job_id)] = record
            self._persist()

    async def get(self, job_id: UUID) -> VectorizerJobRecord | None:
        async with self._lock:
            return self._records.get(str(job_id))

    async def list_all(self) -> list[VectorizerJobRecord]:
        async with self._lock:
            return sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)

    async def delete(self, job_id: UUID) -> None:
        async with self._lock:
            self._records.pop(str(job_id), None)
            self._persist()
