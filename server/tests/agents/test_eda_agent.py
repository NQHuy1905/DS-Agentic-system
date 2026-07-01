"""End-to-end test of the EDAService event stream: start -> contract interrupt ->
resume -> findings -> review interrupt -> resume -> report. Async is driven via
asyncio.run (no pytest-asyncio dependency); LLM + dataset are local fixtures.
"""
from __future__ import annotations

import asyncio

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agents.eda.orchestrator import OrchestratorDecision
from app.agents.eda_agent import EDAService
from app.ingestion import storage
from app.models.eda_schemas import ColumnDtype, ExpectationModel

_CSV = b"a,b,c\n1,1.5,x\n2,2.5,y\n3,3.5,x\n4,4.5,y\n5,5.5,x\n"


class _Meta:
    objective = "predict outcome"
    grain = "one row per record"
    provenance = "unit-test fixture"


class _Structured:
    def __init__(self, schema, parent):
        self._schema, self._parent = schema, parent

    def invoke(self, prompt):
        name = self._schema.__name__
        if name == "ExpectationModel":
            # Expect object but the column is int -> a dtype mismatch finding is produced.
            return ExpectationModel(expected_dtypes=[ColumnDtype(column="a", expected_dtype="object")])
        if name == "OrchestratorDecision":
            d = self._parent.decisions[self._parent.i]
            self._parent.i += 1
            return d
        return _Meta()


class _FakeLLM:
    def __init__(self, decisions):
        self.decisions, self.i = decisions, 0

    def with_structured_output(self, schema):
        return _Structured(schema, self)

    def invoke(self, prompt):
        class _R:
            content = "Stub prose. [unverified] none."
        return _R()


def _types(events):
    return [e.type for e in events]


async def _drive_full_run():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _drive_full_run_body(conn)
    finally:
        # Close the connection so its aiosqlite background thread exits and the
        # test process can shut down cleanly.
        await conn.close()


async def _drive_full_run_body(conn):
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    service = EDAService(saver)

    ref = storage.save_upload(_CSV, "svc_fixture.csv")
    decisions = [
        OrchestratorDecision(action="run_tool", tool="first_contact"),
        OrchestratorDecision(action="run_tool", tool="structural"),
        OrchestratorDecision(action="synthesize"),
    ]
    llm = _FakeLLM(decisions)
    run_id = "svc-run-1"

    # 1) Start -> runs framing, pauses at the contract interrupt.
    await service.start(run_id, llm, ref, "raw objective")
    await service._tasks[run_id]
    assert service.status(run_id) == "interrupted"
    ev = service.replay(run_id, 0)
    assert _types(ev) == ["phase_start", "interrupt"]
    assert ev[-1].checkpoint == "contract"
    assert ev[-1].payload["expectations"] is not None

    # 2) Confirm the contract -> loops through tools, pauses at the review interrupt.
    seen = ev[-1].id
    await service.resume(run_id, ref, "contract", {"objective": "confirmed"})
    await service._tasks[run_id]
    assert service.status(run_id) == "interrupted"
    ev2 = service.replay(run_id, seen)
    assert "phase_start" in _types(ev2) and "finding" in _types(ev2)
    assert ev2[-1].type == "interrupt" and ev2[-1].checkpoint == "review"

    # 3) Approve review -> synthesizes, emits report_ready, ends.
    seen2 = ev2[-1].id
    await service.resume(run_id, ref, "review", {})
    await service._tasks[run_id]
    assert service.status(run_id) == "done"
    ev3 = service.replay(run_id, seen2)
    report_events = [e for e in ev3 if e.type == "report_ready"]
    assert report_events, "expected a report_ready event"
    report_ref = report_events[0].report_url.rsplit("/", 1)[-1]
    assert storage.path_for(report_ref).exists()

    # Monotonic, gapless-enough ids across the whole run (frontend dedups on these).
    all_ids = [e.id for e in service.replay(run_id, 0)]
    assert all_ids == sorted(all_ids) and len(all_ids) == len(set(all_ids))

    # api_key store cleared once the run is terminal.
    assert run_id not in service._llms


def test_service_full_run_event_stream():
    asyncio.run(_drive_full_run())


async def _resume_not_paused():
    conn = await aiosqlite.connect(":memory:")
    try:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        svc = EDAService(saver)
        # Simulate an actively-running (not paused) run with a live session.
        svc._llms["r"] = object()
        svc._buffers["r"] = []
        svc._status["r"] = "running"
        try:
            await svc.resume("r", "ref", "contract", {})
            assert False, "expected ValueError for a non-paused run"
        except ValueError:
            pass
    finally:
        await conn.close()


def test_resume_rejected_when_not_paused():
    """A duplicate/early resume must not spawn a second driver on the same thread."""
    asyncio.run(_resume_not_paused())


class _BoomLLM:
    """Simulates a provider failure mid-call, with a secret in the exception text."""

    def with_structured_output(self, schema):
        class _B:
            def invoke(self, prompt):
                raise RuntimeError("provider 500: leaked sk-secret-abc123")
        return _B()

    def invoke(self, prompt):
        raise RuntimeError("provider 500: leaked sk-secret-abc123")


async def _drive_llm_failure():
    conn = await aiosqlite.connect(":memory:")
    try:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        service = EDAService(saver)
        ref = storage.save_upload(_CSV, "err_fixture.csv")
        await service.start("err-run", _BoomLLM(), ref, "obj")
        await service._tasks["err-run"]
        assert service.status("err-run") == "error"
        ev = service.replay("err-run", 0)
        assert ev[-1].type == "error"
        assert ev[-1].message == "The analysis run failed unexpectedly."   # generic
        assert "sk-secret" not in ev[-1].message                            # raw detail not leaked
        assert "err-run" not in service._llms                               # secret store cleared
    finally:
        await conn.close()


def test_llm_failure_yields_generic_error_event():
    """An LLM/provider failure ends the run cleanly as an ErrorEvent, no secret leak."""
    asyncio.run(_drive_llm_failure())


def test_state_schema_excludes_secrets():
    """The graph state (and thus the checkpoint) must never carry the api_key/LLM."""
    from app.models.eda_schemas import EDAState
    keys = set(EDAState.__annotations__)
    assert "llm" not in keys and "api_key" not in keys
