"""Integration test: the compiled EDA graph runs framing -> contract interrupt ->
resume -> orchestrator/tool loop -> review interrupt -> resume -> synthesizer, on a
tiny fixture with a fully scripted mock LLM (offline, deterministic).
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.agents.eda.graph import build_graph
from app.agents.eda.orchestrator import OrchestratorDecision
from app.ingestion import storage
from app.models.eda_schemas import (
    Budget,
    ColumnDtype,
    EDAState,
    ExpectationModel,
)

_CSV = b"a,b,c\n1,1.5,x\n2,2.5,y\n3,3.5,x\n4,4.5,y\n5,5.5,x\n"


class _Meta:
    objective = "predict outcome"
    grain = "one row per record"
    provenance = "unit-test fixture"


class _Structured:
    def __init__(self, schema, parent):
        self._schema = schema
        self._parent = parent

    def invoke(self, prompt):
        name = self._schema.__name__
        if name == "ExpectationModel":
            return ExpectationModel(
                expected_dtypes=[
                    ColumnDtype(column="a", expected_dtype="int"),
                    ColumnDtype(column="b", expected_dtype="float"),
                    ColumnDtype(column="c", expected_dtype="object"),
                ]
            )
        if name == "OrchestratorDecision":
            d = self._parent.decisions[self._parent.i]
            self._parent.i += 1
            return d
        return _Meta()  # framing metadata schema


class _FakeLLM:
    """Scripts orchestrator decisions; returns canned structured/prose output."""

    def __init__(self, decisions):
        self.decisions = decisions
        self.i = 0

    def with_structured_output(self, schema):
        return _Structured(schema, self)

    def invoke(self, prompt):
        class _R:
            content = "Stub prose. [unverified] no extra claims."
        return _R()


def _initial_state(ref: str) -> EDAState:
    return EDAState(
        dataset_ref=ref, run_id="t1", objective="", grain="", provenance="",
        expectations=None, ledger=[], completed_passes=[], open_surprises=[],
        budget=Budget(max_probes=5), next_action="", report=None,
    )


def test_full_graph_interrupt_resume_flow():
    ref = storage.save_upload(_CSV, "graph_fixture.csv")
    decisions = [
        OrchestratorDecision(action="run_tool", tool="first_contact"),
        OrchestratorDecision(action="run_tool", tool="structural"),
        OrchestratorDecision(action="synthesize"),
    ]
    llm = _FakeLLM(decisions)
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "t1", "llm": llm}}

    # Run to the first (contract) interrupt.
    graph.invoke(_initial_state(ref), config)
    snap = graph.get_state(config)
    assert snap.next == ("contract_gate",)
    assert snap.values["expectations"] is not None          # framing populated priors
    assert snap.values["objective"] == "predict outcome"

    # Human confirms an edited contract, then resume to the review interrupt.
    graph.update_state(config, {"objective": "confirmed objective"})
    graph.invoke(None, config)
    snap = graph.get_state(config)
    assert snap.next == ("review_gate",)
    assert set(snap.values["completed_passes"]) == {"first_contact", "structural"}
    assert snap.values["objective"] == "confirmed objective"

    # Approve review, resume to completion.
    graph.invoke(None, config)
    final = graph.get_state(config)
    assert final.next == ()                                  # reached END
    assert final.values["report"]                            # synthesizer wrote a report_ref
    assert storage.path_for(final.values["report"]).exists()


def test_budget_guard_forces_synthesis_over_llm_request():
    """Even if the LLM keeps asking to run tools, the probe budget forces review."""
    ref = storage.save_upload(_CSV, "graph_budget.csv")
    # Always asks to run a tool; only the deterministic guard can end the loop.
    decisions = [OrchestratorDecision(action="run_tool", tool="univariate")] * 10
    llm = _FakeLLM(decisions)
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "b1", "llm": llm}}

    state = _initial_state(ref)
    state["budget"] = Budget(max_probes=2)
    graph.invoke(state, config)  # -> contract interrupt
    # Resume through both interrupts to completion.
    for _ in range(6):
        if graph.get_state(config).next == ():
            break
        graph.invoke(None, config)
    final = graph.get_state(config)
    assert final.next == ()
    # LLM asked for 10 univariate runs; guards (repeat circuit-breaker + budget)
    # bound it and force synthesis anyway.
    assert final.values["budget"].probes_spent <= 2
    assert final.values["report"]
