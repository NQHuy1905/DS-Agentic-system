"""Pure-LLM orchestrator: picks the next mechanical pass or ends the analysis.

The LLM only advises. The runaway/junior-loop risk of a pure-LLM router is bound
by DETERMINISTIC guards in `route_orchestrator` (loop control lives in code, not
the prompt): a hard probe-budget stop and a repeat/coverage circuit-breaker that
forces the graph toward synthesis rather than trusting the model to stop.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel
from typing_extensions import Literal

from app.core.prompt_loader import render_prompt
from app.models.eda_schemas import EDAState

# Standard mechanical battery run before synthesis. `drift` is intentionally not
# here — it needs a reference dataset and is exercised via the two-batch path.
STANDARD_BATTERY = ["first_contact", "structural", "univariate", "bivariate"]


class OrchestratorDecision(BaseModel):
    action: Literal["run_tool", "chase", "synthesize"]
    tool: Optional[str] = None
    reason: str = ""


def _ledger_summary(state: EDAState) -> str:
    ledger = state.get("ledger", [])  # type: ignore[call-overload]
    if not ledger:
        return "(none yet)"
    return "\n".join(f"- {f.severity} | {f.description[:100]}" for f in ledger[-15:])


def orchestrator(state: EDAState, config: dict) -> dict:
    """LLM planner node — writes `next_action` ('run_tool:<name>' or 'synthesize')."""
    llm = config["configurable"]["llm"]
    completed = state.get("completed_passes", [])  # type: ignore[call-overload]
    remaining = [t for t in STANDARD_BATTERY if t not in completed]
    budget = state["budget"]

    prompt = render_prompt(
        "orchestrator", "system",
        objective=state.get("objective", ""),
        completed=", ".join(completed) or "(none)",
        remaining=", ".join(remaining) or "(none)",
        ledger_summary=_ledger_summary(state),
        probes_spent=budget.probes_spent,
        max_probes=budget.max_probes,
    )
    decision: OrchestratorDecision = llm.with_structured_output(OrchestratorDecision).invoke(prompt)

    if decision.action == "run_tool" and decision.tool:
        return {"next_action": f"run_tool:{decision.tool}"}
    if decision.action == "chase":
        return {"next_action": "chase"}
    return {"next_action": "synthesize"}


def route_orchestrator(state: EDAState) -> str:
    """Deterministic loop control — the real bound on the pure-LLM router.

    Forces the run toward review/synthesis when the budget is spent or when the
    planner re-selects a completed/unknown pass (repeat with no new coverage),
    so a misbehaving router cannot loop indefinitely.
    """
    budget = state["budget"]
    if budget.probes_spent >= budget.max_probes:
        return "review_gate"

    action = state.get("next_action", "synthesize")  # type: ignore[call-overload]
    if action.startswith("run_tool:"):
        tool = action.split(":", 1)[1]
        completed = state.get("completed_passes", [])  # type: ignore[call-overload]
        # Circuit-breaker: a repeat or an off-battery pick makes no new progress.
        if tool not in STANDARD_BATTERY or tool in completed:
            return "review_gate"
        return "tool_runner"
    if action == "chase":
        # Bounded by the hypothesis budget, which also indexes which surprise is
        # chased next; when spent or all surprises are chased, advance to review.
        surprises = state.get("open_surprises", [])  # type: ignore[call-overload]
        if budget.hypo_spent >= budget.max_hypo_iters or budget.hypo_spent >= len(surprises):
            return "review_gate"
        return "hypothesis"
    return "review_gate"
