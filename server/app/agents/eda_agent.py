"""EDA agent service: drives the compiled graph and bridges it to SSE.

Responsibilities:
- Own one compiled graph + checkpointer; run many runs keyed by run_id (thread_id).
- Translate graph node updates into the typed EDAEvent stream, assigning each a
  monotonic id and appending to a DURABLE per-run buffer so `/stream` can replay
  from Last-Event-ID after a reconnect or a human-checkpoint resume.
- Keep the user's LLM (and its api_key) in an in-memory per-run store only —
  never in graph state / checkpoint / logs. Deleted when the run terminates.
- Lease the dataset for the run's lifetime so TTL cleanup can't delete it mid-run.
- Register the driving task so shutdown / client-disconnect can cancel it instead
  of letting the pure-LLM loop burn tokens with no consumer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.agents.eda.graph import build_graph
from app.ingestion import storage
from app.models.eda_events import (
    ErrorEvent,
    FindingEvent,
    InterruptEvent,
    PhaseStartEvent,
    ReportReadyEvent,
)
from app.models.eda_schemas import Budget, EDAState

# Run lifecycle status.
_RUNNING = "running"
_INTERRUPTED = "interrupted"
_DONE = "done"
_ERROR = "error"

_MAX_CONCURRENT_RUNS = 4

logger = logging.getLogger(__name__)


class EDAService:
    def __init__(self, checkpointer: Optional[object] = None):
        self._graph = build_graph(checkpointer)
        self._buffers: dict[str, list[Any]] = {}
        self._status: dict[str, str] = {}
        self._llms: dict[str, Any] = {}
        self._next_id: dict[str, int] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT_RUNS)

    # -- buffer / event helpers ------------------------------------------------
    def _emit(self, run_id: str, event: Any) -> None:
        nid = self._next_id.get(run_id, 0) + 1
        self._next_id[run_id] = nid
        event.id = nid
        self._buffers.setdefault(run_id, []).append(event)

    def replay(self, run_id: str, last_event_id: int) -> list[Any]:
        return [e for e in self._buffers.get(run_id, []) if e.id > last_event_id]

    def status(self, run_id: str) -> Optional[str]:
        return self._status.get(run_id)

    def known(self, run_id: str) -> bool:
        # A run exists once start() has registered its buffer (set synchronously
        # before the driving task is scheduled), so this is race-free vs /stream.
        return run_id in self._buffers

    def _config(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id, "llm": self._llms[run_id]}}

    def _cleanup_secret(self, run_id: str) -> None:
        # api_key lifecycle: drop the LLM (and its key) once no further resume is possible.
        self._llms.pop(run_id, None)

    # -- event translation -----------------------------------------------------
    def _translate(self, run_id: str, node: str, update: dict) -> None:
        if node == "framing":
            self._emit(run_id, PhaseStartEvent(id=0, phase="framing"))
        elif node == "tool_runner":
            tool = (update.get("completed_passes") or ["tool"])[0]
            self._emit(run_id, PhaseStartEvent(id=0, phase=tool))
            for finding in update.get("ledger", []):
                self._emit(run_id, FindingEvent(id=0, finding=finding))
        elif node == "synthesizer":
            ref = update.get("report")
            if ref:
                self._emit(run_id, ReportReadyEvent(id=0, report_url=f"/api/v1/eda/report/{ref}"))

    async def _finalize_step(self, run_id: str, dataset_ref: str) -> None:
        """After an astream pass ends, emit an interrupt or terminal signal."""
        snap = await self._graph.aget_state(self._config(run_id))
        nxt = tuple(snap.next)
        values = snap.values
        if "contract_gate" in nxt:
            exp = values.get("expectations")
            payload = {
                "objective": values.get("objective", ""),
                "grain": values.get("grain", ""),
                "expectations": exp.model_dump() if exp is not None else None,
            }
            self._emit(run_id, InterruptEvent(id=0, checkpoint="contract", payload=payload))
            self._status[run_id] = _INTERRUPTED
        elif "review_gate" in nxt:
            ledger = values.get("ledger", [])
            n_crit = sum(1 for f in ledger if f.severity == "critical")
            payload = {"summary": f"{len(ledger)} findings ({n_crit} critical). Review before the report."}
            self._emit(run_id, InterruptEvent(id=0, checkpoint="review", payload=payload))
            self._status[run_id] = _INTERRUPTED
        else:
            self._status[run_id] = _DONE
            storage.release(dataset_ref, run_id)
            self._cleanup_secret(run_id)

    async def _drive(self, run_id: str, stream_input: Optional[dict], dataset_ref: str) -> None:
        """Drive the graph one pass (start or resume) until it interrupts or ends."""
        self._status[run_id] = _RUNNING
        try:
            async with self._sem:
                async for chunk in self._graph.astream(
                    stream_input, self._config(run_id), stream_mode="updates"
                ):
                    # chunk == {node_name: partial_update}
                    for node, update in chunk.items():
                        if isinstance(update, dict):
                            self._translate(run_id, node, update)
            await self._finalize_step(run_id, dataset_ref)
        except asyncio.CancelledError:
            raise
        except Exception:  # surface as a terminal ErrorEvent (generic to the client)
            # Full detail stays server-side; the SSE consumer must not receive raw
            # exception text (could carry paths, data values, or provider errors).
            logger.exception("EDA run %s failed", run_id)
            self._emit(run_id, ErrorEvent(id=0, message="The analysis run failed unexpectedly."))
            self._status[run_id] = _ERROR
            storage.release(dataset_ref, run_id)
            self._cleanup_secret(run_id)

    # -- public API ------------------------------------------------------------
    async def start(self, run_id: str, llm: Any, dataset_ref: str, objective: str) -> None:
        self._llms[run_id] = llm
        self._buffers[run_id] = []
        self._next_id[run_id] = 0
        storage.lease(dataset_ref, run_id)
        initial: EDAState = {
            "dataset_ref": dataset_ref, "run_id": run_id, "objective": objective,
            "grain": "", "provenance": "", "expectations": None, "ledger": [],
            "completed_passes": [], "open_surprises": [], "budget": Budget(),
            "next_action": "", "report": None,
        }
        self._tasks[run_id] = asyncio.create_task(self._drive(run_id, initial, dataset_ref))

    async def resume(self, run_id: str, dataset_ref: str, checkpoint: str, response: dict) -> None:
        if run_id not in self._llms:
            raise KeyError(f"Run {run_id} is not resumable (no active session)")
        # Reject a duplicate/early resume: a second astream on the same checkpointer
        # thread while one is already running would corrupt the checkpoint.
        if self._status.get(run_id) != _INTERRUPTED:
            raise ValueError(f"Run {run_id} is not paused at a checkpoint")
        # Apply the human's edits before resuming. Only the contract gate mutates state.
        if checkpoint == "contract" and response:
            edits = {k: response[k] for k in ("objective", "grain") if k in response}
            if edits:
                await self._graph.aupdate_state(self._config(run_id), edits)
        self._tasks[run_id] = asyncio.create_task(self._drive(run_id, None, dataset_ref))

    async def cancel(self, run_id: str, dataset_ref: str) -> None:
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        storage.release(dataset_ref, run_id)
        self._cleanup_secret(run_id)
