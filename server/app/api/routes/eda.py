"""EDA HTTP surface: start a run, stream its events (SSE), resume at a human
checkpoint, download the report.

The graph is driven by a background task into a durable per-run event buffer;
`/stream` replays that buffer honoring Last-Event-ID, so a reconnect or a
post-resume second half of the run is delivered without loss. The api_key lives
only in the service's in-memory per-run store — never in state, checkpoint, or logs.

Boundary: no auth. Single-user / localhost is an explicit non-goal to secure here.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from uuid import uuid4

import aiosqlite
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from app.agents.eda_agent import EDAService
from app.core.llm_factory import create_llm
from app.ingestion import storage
from app.models.eda_events import serialize
from app.models.workflow_schemas import LLMConfig

router = APIRouter(prefix="/eda", tags=["eda"])

_CHECKPOINT_DB = Path(__file__).resolve().parents[3] / ".eda_runs" / "checkpoints.db"
_REF_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")  # opaque uuid-like storage refs only

_service: EDAService | None = None
_service_lock = asyncio.Lock()
# Maps run_id -> dataset_ref so resume/stream can release the lease on terminal.
_run_datasets: dict[str, str] = {}


async def _get_service() -> EDAService:
    global _service
    if _service is None:
        async with _service_lock:
            if _service is None:
                _CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(str(_CHECKPOINT_DB))
                _service = EDAService(AsyncSqliteSaver(conn))
    return _service


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    llm_config: LLMConfig
    dataset_ref: str
    objective: str = ""


class ResumeRequest(BaseModel):
    checkpoint: str
    response: dict = {}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post("/run")
async def run_eda(req: RunRequest) -> dict:
    # path_for validates the ref is a well-formed, known, contained dataset —
    # rejecting bad/unknown/traversal refs with a clean 400 instead of a 500.
    try:
        storage.path_for(req.dataset_ref)
    except (ValueError, FileNotFoundError, PermissionError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or unknown dataset_ref")
    service = await _get_service()
    llm = create_llm(req.llm_config.provider, req.llm_config.model, req.llm_config.api_key)
    run_id = uuid4().hex
    _run_datasets[run_id] = req.dataset_ref
    await service.start(run_id, llm, req.dataset_ref, req.objective)
    return {"run_id": run_id}


@router.get("/stream/{run_id}")
async def stream_eda(run_id: str, request: Request) -> StreamingResponse:
    service = await _get_service()
    if not service.known(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown run")
    last_id = int(request.headers.get("last-event-id", 0) or 0)

    async def event_gen():
        sent = last_id
        while True:
            # On disconnect just end this generator. The run continues into the
            # durable buffer (its length is bounded by the probe budget), so a
            # reconnecting EventSource replays from Last-Event-ID without loss —
            # cancelling here would instead kill a resumable/interrupted run.
            if await request.is_disconnected():
                return
            for event in service.replay(run_id, sent):
                sent = event.id
                yield serialize(event)
            state = service.status(run_id)
            # Terminal states with a fully-drained buffer end the stream.
            if state in ("done", "error") and not service.replay(run_id, sent):
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/resume/{run_id}")
async def resume_eda(run_id: str, req: ResumeRequest) -> dict:
    service = await _get_service()
    dataset_ref = _run_datasets.get(run_id, "")
    try:
        await service.resume(run_id, dataset_ref, req.checkpoint, req.response)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown or expired run") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Run is not awaiting a checkpoint") from exc
    return {"status": "resuming"}


@router.get("/report/{ref}")
async def get_report(ref: str) -> FileResponse:
    if not _REF_RE.match(ref):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid report ref")
    try:
        path = storage.path_for(ref).resolve()
    except Exception as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found") from exc
    base = storage._temp_dir().resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return FileResponse(path, media_type="text/markdown", filename=storage.filename_for(ref))
