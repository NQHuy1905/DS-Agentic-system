"""Shared contract for the EDA agentic workflow.

This module is the frozen spine every EDA phase codes against: graph state,
domain objects, typed tool observations, and the LLM-injection convention.

`from __future__ import annotations` is REQUIRED — the server runs Python 3.9,
where PEP 604 `X | None` unions are evaluated at runtime by pydantic and raise
TypeError. Deferring annotation evaluation makes the modern syntax safe here.

LLM injection: graph nodes receive (state, config) only. The per-request LLM is
passed via `config["configurable"]["llm"]`, NEVER stored in EDAState — keeping
the api_key out of the SQLite checkpoint and any trace sink.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel
from typing_extensions import TypedDict


# --------------------------------------------------------------------------- #
# Expectation model (Phase 0 framing output).
# Structured-output-friendly: list-of-objects, never open dict/tuple, so
# Gemini's structured-output schema subset accepts it (.with_structured_output).
# --------------------------------------------------------------------------- #
class ColumnDtype(BaseModel):
    column: str
    expected_dtype: str


class ColumnRange(BaseModel):
    column: str
    min: float
    max: float


class ColumnNullPrior(BaseModel):
    column: str
    expected_null_rate: float


class ColumnCategories(BaseModel):
    column: str
    valid_values: list[str]


class ExpectationModel(BaseModel):
    expected_dtypes: list[ColumnDtype] = []
    ranges: list[ColumnRange] = []
    null_priors: list[ColumnNullPrior] = []
    row_magnitude: Optional[int] = None
    valid_categories: list[ColumnCategories] = []
    notes: str = ""


# --------------------------------------------------------------------------- #
# Findings & working memory.
# --------------------------------------------------------------------------- #
class Finding(BaseModel):
    id: str
    phase: str
    column: Optional[str] = None
    observed: Any = None
    expected: Any = None
    severity: Literal["info", "warn", "critical"]
    description: str
    # Grounding keystone: REQUIRED, no default. A finding cannot exist without
    # pointing back to the observation that produced it — this anchors the
    # chain tool-output -> ledger -> traceable report claim.
    evidence_ref: str
    root_cause: Optional[str] = None
    decision: Optional[str] = None


class Surprise(BaseModel):
    id: str
    finding_id: str
    question: str
    chased: bool = False


class Budget(BaseModel):
    max_probes: int = 20
    max_hypo_iters: int = 5
    probes_spent: int = 0
    hypo_spent: int = 0


# --------------------------------------------------------------------------- #
# Typed tool observations. Pinned here so the Phase 3 producer and the Phase 7
# consumer cannot drift. Every observation carries `id` (referenced by
# Finding.evidence_ref) and `tool` (the discriminator).
# Inner shapes are filled by Phase 3; the envelope fields are the contract.
# --------------------------------------------------------------------------- #
class _ObservationBase(BaseModel):
    id: str
    tool: str
    seed: Optional[int] = None  # seeded sampling -> reproducible (guard G_mech)
    truncated: bool = False     # output-size / top-k cap was applied


class FirstContactObs(_ObservationBase):
    tool: Literal["first_contact"] = "first_contact"
    payload: dict = {}


class StructuralObs(_ObservationBase):
    tool: Literal["structural"] = "structural"
    payload: dict = {}


class UnivariateObs(_ObservationBase):
    tool: Literal["univariate"] = "univariate"
    payload: dict = {}


class BivariateObs(_ObservationBase):
    tool: Literal["bivariate"] = "bivariate"
    payload: dict = {}


class DriftObs(_ObservationBase):
    tool: Literal["drift"] = "drift"
    payload: dict = {}


Observation = Union[
    FirstContactObs, StructuralObs, UnivariateObs, BivariateObs, DriftObs
]


# --------------------------------------------------------------------------- #
# Graph state. Accumulator fields MUST use reducers — LangGraph's default
# channel is last-value-wins (overwrite); the orchestrator loop appends across
# iterations, so without reducers earlier findings are silently lost.
# --------------------------------------------------------------------------- #
def _budget_reducer(old: Budget, new: Budget) -> Budget:
    """Budget is mutated (counters advance), not appended — keep latest."""
    return new


class EDAState(TypedDict):
    dataset_ref: str
    run_id: str
    objective: str
    grain: str
    provenance: str
    expectations: Optional[ExpectationModel]
    ledger: Annotated[list[Finding], operator.add]
    completed_passes: Annotated[list[str], operator.add]
    open_surprises: Annotated[list[Surprise], operator.add]
    budget: Annotated[Budget, _budget_reducer]
    next_action: str
    report: Optional[str]
    # NOTE: no llm / api_key here — secrets must not enter checkpointed state.
