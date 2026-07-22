"""
Websocket hub for pushing live job state (stats + operation feed) to the React
dashboard. Clients subscribe by job_id, or "*" for every job (used by the
dashboard's aggregate view and its live operations feed). VectorizerEngine calls
broadcast() on every batch processed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.schemas import VectorizerJobRecord

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, job_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(job_id, set()).add(ws)

    def disconnect(self, job_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(job_id)
        if conns and ws in conns:
            conns.remove(ws)
        if conns is not None and not conns:
            self._connections.pop(job_id, None)

    async def broadcast(self, record: VectorizerJobRecord) -> None:
        job_id = str(record.job_id)
        payload = record.model_dump_json()
        for target_id in (job_id, "*"):
            for ws in list(self._connections.get(target_id, set())):
                try:
                    await ws.send_text(payload)
                except Exception:  # noqa: BLE001
                    self.disconnect(target_id, ws)


manager = ConnectionManager()


async def broadcast_progress(record: VectorizerJobRecord) -> None:
    await manager.broadcast(record)


@router.websocket("/ws/jobs/{job_id}")
async def job_progress_ws(websocket: WebSocket, job_id: str) -> None:
    await manager.connect(job_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(job_id, websocket)


@router.websocket("/ws/jobs")
async def all_jobs_ws(websocket: WebSocket) -> None:
    """Subscribe to updates for every job -- the dashboard and its operations feed."""
    await manager.connect("*", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect("*", websocket)
