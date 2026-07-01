"""Deterministic G_orch routing guard tests — no LLM."""
from __future__ import annotations

from app.agents.eda.orchestrator import route_orchestrator
from app.models.eda_schemas import Budget, Surprise


def _state(next_action, *, completed=None, surprises=None, budget=None):
    return {
        "next_action": next_action,
        "completed_passes": completed or [],
        "open_surprises": surprises or [],
        "budget": budget or Budget(),
    }


def test_new_tool_routes_to_tool_runner():
    assert route_orchestrator(_state("run_tool:univariate")) == "tool_runner"


def test_repeat_tool_circuit_breaks_to_review():
    assert route_orchestrator(_state("run_tool:univariate", completed=["univariate"])) == "review_gate"


def test_off_battery_tool_routes_to_review():
    assert route_orchestrator(_state("run_tool:not_a_tool")) == "review_gate"


def test_probe_budget_spent_forces_review():
    b = Budget(max_probes=2, probes_spent=2)
    assert route_orchestrator(_state("run_tool:univariate", budget=b)) == "review_gate"


def test_synthesize_routes_to_review():
    assert route_orchestrator(_state("synthesize")) == "review_gate"


def test_chase_with_open_surprise_routes_to_hypothesis():
    s = [Surprise(id="s", finding_id="f", question="?")]
    assert route_orchestrator(_state("chase", surprises=s)) == "hypothesis"


def test_chase_with_no_surprise_routes_to_review():
    assert route_orchestrator(_state("chase")) == "review_gate"


def test_chase_over_hypo_budget_routes_to_review():
    b = Budget(max_hypo_iters=1, hypo_spent=1)
    s = [Surprise(id="s", finding_id="f", question="?"), Surprise(id="s2", finding_id="f2", question="?")]
    assert route_orchestrator(_state("chase", surprises=s, budget=b)) == "review_gate"
