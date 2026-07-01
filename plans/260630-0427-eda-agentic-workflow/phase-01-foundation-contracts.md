---
phase: 1
title: "Foundation Contracts"
status: completed
priority: P1
effort: "1.5d"
dependencies: []
---

# Phase 1: Foundation Contracts

## Overview
Define the shared state schema, SSE event schema, observation schema, LLM-injection contract, and API contract that every other phase codes against. This is the spine — freeze it before fan-out. No business logic, only types + stub endpoints. **Red-team raised the bar here: the original contract was underspecified in 5 ways that would only break at Phase 9 integration; all are fixed below.**

**Guardrail overlay (refined diagram):** the contract carries the grounding keystone — `Finding.evidence_ref` (required) anchors the chain `tool output → evidence pointer in Ledger → traceable claim in report`. Six guards distribute into their owning phases (G_frame→P6, G_mech→P3, G_orch→P9, G_sandbox→P4, G_eval→P7, G_synth→P8); only the evidence-pointer field lives here because P7 produces it and P8 consumes it.

## Requirements
- Functional: Pydantic models for `ExpectationModel`, `Finding`, `Surprise`, `Budget`, typed per-tool `Observation`s; the `EDAState` graph state WITH reducers; the LLM-injection contract; SSE event union; request/response models for upload/run/resume/stream.
- Non-functional: stable field names (renames ripple across all parallel work); **must import on Python 3.9.21** (the verified env); fully typed.

## Architecture

### Python 3.9 compatibility (mandatory)
The server runs **Python 3.9.21**. PEP 604 `X | None` unions are evaluated at runtime by pydantic v2 and raise `TypeError` on 3.9. **Every model module MUST start with `from __future__ import annotations`** (defers annotation evaluation), OR use `Optional[...]`/`Union[...]` from `typing`. This is not optional — without it the W0 foundation does not import and blocks all fan-out.

### `eda_schemas.py`
```python
from __future__ import annotations          # REQUIRED for 3.9
import operator
from typing import Annotated, Any, Optional, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, SecretStr

# --- Structured-output-friendly: avoid open dicts/tuples so Gemini's schema
#     subset accepts ExpectationModel via .with_structured_output (see Phase 6). ---
class ColumnRange(BaseModel):
    column: str
    min: float
    max: float

class ColumnDtype(BaseModel):
    column: str
    expected_dtype: str

class ColumnNullPrior(BaseModel):
    column: str
    expected_null_rate: float

class ColumnCategories(BaseModel):
    column: str
    valid_values: list[str]

class ExpectationModel(BaseModel):
    expected_dtypes: list[ColumnDtype]
    ranges: list[ColumnRange]
    null_priors: list[ColumnNullPrior]
    row_magnitude: Optional[int] = None
    valid_categories: list[ColumnCategories]
    notes: str = ""

class Finding(BaseModel):
    id: str
    phase: str
    column: Optional[str] = None
    observed: Any
    expected: Any
    severity: Literal["info", "warn", "critical"]
    description: str
    # GROUNDING KEYSTONE (guardrail overlay): REQUIRED, no default — a Finding
    # cannot be constructed without pointing back to the Observation that
    # produced it. This is the evidence chain tool-output → ledger → report.
    evidence_ref: str                        # = Observation.id that produced this
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

# --- Typed observation contract (pinned here so Phase 3 producer and Phase 7
#     consumer cannot diverge — red-team Critical). One model per tool.
#     Each Observation carries an `id` so Findings can reference it
#     (evidence_ref) — the grounding chain's anchor. ---
class FirstContactObs(BaseModel): ...      # has `id: str`, `tool: str`; shape per Phase 3
class StructuralObs(BaseModel): ...
class UnivariateObs(BaseModel): ...
class BivariateObs(BaseModel): ...
class DriftObs(BaseModel): ...
Observation = Annotated[
    "FirstContactObs | StructuralObs | UnivariateObs | BivariateObs | DriftObs",
    "discriminated by `tool` field"
]

def _budget_reducer(old: Budget, new: Budget) -> Budget:
    return new                              # last-write-wins for the mutable counter

class EDAState(TypedDict):
    dataset_ref: str
    run_id: str
    objective: str
    grain: str
    provenance: str
    expectations: Optional[ExpectationModel]
    # REDUCERS REQUIRED — default channel is last-value-wins (overwrite); the
    # orchestrator loop appends across iterations, so these MUST accumulate.
    ledger: Annotated[list[Finding], operator.add]
    completed_passes: Annotated[list[str], operator.add]
    open_surprises: Annotated[list[Surprise], operator.add]
    budget: Annotated[Budget, _budget_reducer]
    next_action: str
    report: Optional[str]
    # NOTE: NO api_key / llm here — secrets must not enter checkpointed state.
```

### LLM injection contract (graph nodes get state only)
LangGraph node functions receive **state + `RunnableConfig`**, not arbitrary positional args. The per-request LLM (built via `create_llm(provider, model, api_key)`) is injected via `config["configurable"]["llm"]`, NOT via `EDAState` (keeps the api_key out of the SQLite checkpoint + LangSmith traces). **Every leaf node reads `llm = config["configurable"]["llm"]`.** Phases 6/7/8/9/10 re-spec their signatures to `node(state, config)` accordingly.

### `eda_events.py`
Discriminated union on `type`: `PhaseStartEvent`, `FindingEvent`, `InterruptEvent{checkpoint,payload}`, `ReportReadyEvent{report_url}`, `ErrorEvent{message}`. Each event carries a monotonic `id: int` for SSE `Last-Event-ID` replay. `serialize(event) -> str` emits `id: {id}\ndata: {json}\n\n`.

### API contract
- `POST /eda/upload` (multipart) → `{dataset_ref, meta}`
- `POST /eda/run` `{llm_config, dataset_ref, objective}` → `{run_id}`; starts a background task driving the graph into a **durable per-run event buffer** (not an ephemeral queue).
- `GET /eda/stream/{run_id}` → SSE replayed from the buffer honoring `Last-Event-ID` (so post-resume events + reconnects are not lost — red-team Critical).
- `POST /eda/resume/{run_id}` `{checkpoint, response}` → resumes interrupt. The api_key is held in a short-lived in-memory secret store keyed by `run_id` (never persisted) so resume can rebuild the LLM.
- `GET /eda/report/{ref}` → download (validate ref, see Phase 2 path-traversal fix).

### Dependencies seeded (Phase 1 owns requirements.txt exclusively)
Add: `pandas`, `pyarrow`, `python-multipart` (multipart upload — else `/upload` 500s), `langgraph-checkpoint-sqlite` (SQLite checkpointer — user choice), `sse-starlette` (or document raw `StreamingResponse`). **Do NOT add `langsmith`** — it is already transitive via langchain-core==0.3.15; an unpinned add risks version skew. Tracing stays env-gated on the transitive dep.

## Related Code Files
- Create: `server/app/models/eda_schemas.py`, `server/app/models/eda_events.py`
- Modify: `server/requirements.txt` (deps above)

## Implementation Steps
1. Seed `requirements.txt` deps (above); install into conda `research`.
2. Write `eda_schemas.py` — `from __future__ import annotations` first line; all models + reducers + typed Observations.
3. Write `eda_events.py` — event union with `id` + `serialize()`.
4. Seed empty package markers: `server/app/{ingestion,tools,sandbox,agents/eda}/__init__.py` + `server/tests/{ingestion,tools,sandbox,agents,e2e}/__init__.py`.
5. Document the LLM-injection contract (`config["configurable"]["llm"]`) at the top of `eda_schemas.py` as the canonical reference for all node authors.
6. Smoke test: **import all models under conda `research` (Python 3.9)**; build a 2-node graph asserting `ledger` ACCUMULATES (reducer works); round-trip `.model_dump()`; assert `api_key`/`SecretStr` never appears in `EDAState`.

## Success Criteria
- [ ] All models import cleanly under conda `research` (Python 3.9.21) — verified, not assumed.
- [ ] 2-node graph test proves `ledger`/`completed_passes`/`open_surprises` accumulate (reducers).
- [ ] Event union discriminates on `type`; events carry `id`.
- [ ] `ExpectationModel` uses list-of-objects (no open dicts/tuples) — Gemini-compatible.
- [ ] LLM-injection contract documented; no secret fields in `EDAState`.
- [ ] All deps (incl. `python-multipart`, `langgraph-checkpoint-sqlite`) seeded.
- [ ] Field names reviewed + frozen.

## Risk Assessment
Risk: field churn after fan-out forces rework everywhere. Mitigation: over-invest in naming review now; the 5 red-team contract fixes (reducers, future-import, llm injection, typed observations, deps) are the high-cost-if-missed items — all locked here before W1/W2.
