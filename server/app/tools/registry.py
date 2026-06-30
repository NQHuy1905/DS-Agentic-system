"""Tool registry for the EDA mechanical profiling pass.

Convention
----------
Each entry in TOOL_REGISTRY maps a tool name (string discriminator that also
matches the `tool` field on the returned Observation) to a callable with the
signature:

    run(df: pd.DataFrame, state: EDAState, **kwargs) -> Observation

The **kwargs slot lets specialised tools (e.g. drift) accept extra arguments
(e.g. reference_df) through run_tool without changing the common interface.

The Phase 9 orchestrator calls run_tool(name, df, state) — or passes keyword
arguments for tools that need them — and receives a typed Observation that
carries an `id` for grounding in Finding.evidence_ref.
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from app.models.eda_schemas import EDAState, Observation

from app.tools import bivariate, drift, first_contact, structural, univariate

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Callable[..., Observation]] = {
    "first_contact": first_contact.run,
    "structural": structural.run,
    "univariate": univariate.run,
    "bivariate": bivariate.run,
    "drift": drift.run,
}


def run_tool(
    name: str,
    df: pd.DataFrame,
    state: EDAState,
    **kwargs: Any,
) -> Observation:
    """Dispatch to a registered tool by name and return its typed Observation.

    Args:
        name: One of the keys in TOOL_REGISTRY.
        df: DataFrame to profile.
        state: Current EDA graph state.
        **kwargs: Optional extra arguments forwarded to the tool (e.g.
            ``reference_df`` for the drift tool).

    Raises:
        KeyError: If `name` is not registered.
    """
    if name not in TOOL_REGISTRY:
        registered = ", ".join(sorted(TOOL_REGISTRY))
        raise KeyError(
            f"Unknown tool {name!r}. Registered tools: {registered}"
        )
    return TOOL_REGISTRY[name](df, state, **kwargs)
