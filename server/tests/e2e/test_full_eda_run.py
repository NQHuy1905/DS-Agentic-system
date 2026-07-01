"""End-to-end: run the full EDA graph against each real CSV dropped in
tests/e2e/fixtures/. Skips cleanly when the folder is empty, so CI stays green
until real datasets are provided. The LLM is scripted (offline); the tools,
evaluator, graph, interrupts, and synthesizer all execute for real on the data.
"""
from __future__ import annotations

import glob
import os

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agents.eda.graph import build_graph
from app.agents.eda.orchestrator import STANDARD_BATTERY, OrchestratorDecision
from app.ingestion import storage
from app.models.eda_schemas import Budget, EDAState, ExpectationModel

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_CSVS = sorted(glob.glob(os.path.join(_FIXTURES, "*.csv")))


class _Meta:
    objective = "explore data quality"
    grain = "one row per record"
    provenance = "provided fixture"


class _Struct:
    def __init__(self, schema, parent):
        self._schema, self._parent = schema, parent

    def invoke(self, prompt):
        name = self._schema.__name__
        if name == "ExpectationModel":
            return ExpectationModel()          # empty priors: valid for any real schema
        if name == "OrchestratorDecision":
            d = self._parent.decisions[self._parent.i]
            self._parent.i += 1
            return d
        return _Meta()


class _LLM:
    def __init__(self, decisions):
        self.decisions, self.i = decisions, 0

    def with_structured_output(self, schema):
        return _Struct(schema, self)

    def invoke(self, prompt):
        class _R:
            content = "Stub narrative."
        return _R()


@pytest.mark.skipif(not _CSVS, reason="no CSVs in tests/e2e/fixtures/")
@pytest.mark.parametrize("csv_path", _CSVS, ids=[os.path.basename(p) for p in _CSVS])
def test_full_run_on_real_csv(csv_path):
    with open(csv_path, "rb") as fh:
        ref = storage.save_upload(fh.read(), os.path.basename(csv_path))

    decisions = [OrchestratorDecision(action="run_tool", tool=t) for t in STANDARD_BATTERY]
    decisions.append(OrchestratorDecision(action="synthesize"))
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": os.path.basename(csv_path), "llm": _LLM(decisions)}}
    state = EDAState(
        dataset_ref=ref, run_id="e2e", objective="", grain="", provenance="",
        expectations=None, ledger=[], completed_passes=[], open_surprises=[],
        budget=Budget(), next_action="", report=None,
    )

    graph.invoke(state, config)                       # -> contract interrupt
    for _ in range(10):                               # resume through both interrupts
        if graph.get_state(config).next == ():
            break
        graph.invoke(None, config)

    final = graph.get_state(config)
    assert final.next == (), "graph did not reach completion"
    assert final.values["report"], "no report produced"
    assert isinstance(final.values["ledger"], list)
    assert storage.path_for(final.values["report"]).exists()
