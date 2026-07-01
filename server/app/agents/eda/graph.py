"""Assemble the EDA StateGraph.

Flow: framing -> [contract interrupt] -> orchestrator loop (tool_runner ->
orchestrator) -> [review interrupt] -> synthesizer -> END.

The two human checkpoints are STATIC `interrupt_before` gates on dedicated no-op
nodes (`contract_gate`, `review_gate`) rather than on the orchestrator, so a
per-iteration loop node is not itself the one-time gate. The graph cannot pass
either gate without an explicit resume.

tool_runner fuses "run a tool" and "evaluate its observation" into one node: the
graph state carries no transient observation channel, so keeping the observation
inside a single node avoids widening the frozen state schema.
"""
from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph

from app.agents.eda.evaluator import evaluate
from app.agents.eda.framing import framing
from app.agents.eda.hypothesis import hypothesis
from app.agents.eda.orchestrator import orchestrator, route_orchestrator
from app.agents.eda.synthesizer import synthesizer
from app.ingestion.loader import load_dataframe
from app.models.eda_schemas import EDAState
from app.tools.registry import run_tool


# langgraph requires every node to update >=1 state key; appending an empty list
# through the `operator.add` completed_passes reducer is a true no-op.
_NOOP_UPDATE = {"completed_passes": []}


def _passthrough(state: EDAState) -> dict:
    """No-op node used purely as a static interrupt anchor."""
    return dict(_NOOP_UPDATE)


def tool_runner(state: EDAState, config: dict) -> dict:
    """Run the orchestrator-selected tool, evaluate its observation into findings."""
    action = state.get("next_action", "")  # type: ignore[call-overload]
    tool = action.split(":", 1)[1] if action.startswith("run_tool:") else action
    df = load_dataframe(state["dataset_ref"])
    obs = run_tool(tool, df, state)
    findings, surprises = evaluate([obs], state.get("expectations"))  # type: ignore[arg-type]
    budget = state["budget"]
    new_budget = budget.model_copy(update={"probes_spent": budget.probes_spent + 1})
    return {
        "ledger": findings,
        "open_surprises": surprises,
        "completed_passes": [tool],
        "budget": new_budget,
    }


def build_graph(checkpointer: Optional[object] = None):
    """Build + compile the EDA graph. Pass a checkpointer for durable/resumable runs."""
    g = StateGraph(EDAState)
    g.add_node("framing", framing)
    g.add_node("contract_gate", _passthrough)
    g.add_node("orchestrator", orchestrator)
    g.add_node("tool_runner", tool_runner)
    g.add_node("hypothesis", hypothesis)
    g.add_node("review_gate", _passthrough)
    g.add_node("synthesizer", synthesizer)

    g.add_edge(START, "framing")
    g.add_edge("framing", "contract_gate")
    g.add_edge("contract_gate", "orchestrator")
    g.add_conditional_edges(
        "orchestrator",
        route_orchestrator,
        {"tool_runner": "tool_runner", "hypothesis": "hypothesis", "review_gate": "review_gate"},
    )
    g.add_edge("tool_runner", "orchestrator")
    g.add_edge("hypothesis", "orchestrator")
    g.add_edge("review_gate", "synthesizer")
    g.add_edge("synthesizer", END)

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=["contract_gate", "review_gate"],
    )
