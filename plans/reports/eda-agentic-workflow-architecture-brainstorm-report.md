# EDA Agentic Workflow — Architecture & Parallel Build Plan

**Date:** 2026-06-30
**Status:** Design approved (pure-LLM orchestrator variant)
**Source idea:** `plans/EDA_ideal.md` + `plans/EDA_ideal_diagram.mmd`

## Problem Statement

Build Workflow 1 (Automated EDA Agent) as a senior-grade, hypothesis-driven loop — not a linear `describe()` pipeline. Agent must carry priors, get surprised by observed-vs-expected gaps, prioritize by downstream impact, and adapt probes based on findings. Output streams to UI + downloadable report.

## Locked Decisions

| Decision | Choice |
|----------|--------|
| Data input | CSV/file upload via UI |
| Tool execution | Hybrid: deterministic pandas (Phases 1–5) + LLM-gen sandboxed code (Phase 6) |
| Scope | Full diagram, built/validated in parallel waves |
| Output | Streamed findings in UI (SSE) + downloadable markdown/JSON report |
| Orchestrator | **Pure-LLM routing** (user choice) w/ coverage-tracking + budget guard in prompt |
| Tracing | LangSmith, env-gated/optional |

## Architecture — LangGraph StateGraph

Shared state is the spine; every node reads/writes it:

```python
class EDAState(TypedDict):
    dataset_ref: str
    objective: str; grain: str; provenance: str
    expectations: ExpectationModel   # schema, ranges, null priors, row magnitude, valid categories
    ledger: list[Finding]            # issue, severity, root cause, decision
    completed_passes: list[str]      # coverage tracking (feeds orchestrator prompt)
    open_surprises: list[Surprise]
    budget: Budget                   # max probes / max hypo iters / spent
    next_action: str
    report: str | None
```

```python
class Finding(BaseModel):
    phase: str; column: str | None
    observed: Any; expected: Any
    severity: Literal["info", "warn", "critical"]
    description: str; root_cause: str | None; decision: str | None
```

**Node flow:** Framing Agent → [interrupt: human confirms contract] → Orchestrator (LLM) ⇄ {5 mechanical tools} → Evaluator → Ledger ⇄ Hypothesis Engine (sandbox) → Synthesizer → [interrupt: human reviews] → Done.

**Pure-LLM Orchestrator:** each loop the LLM picks next probe from available tools, conditioned on ExpectationModel + Ledger + completed_passes + budget. Coverage tracking + budget guard keep it bounded and prevent junior-behavior regression without hard-coded routing.

**Execution:** Phases 1–5 = deterministic pandas tools. Phase 6 = LLM-gen code in locked sandbox (subprocess, restricted imports, no fs/net, time+mem caps).

**Streaming:** FastAPI SSE (`StreamingResponse`) emits typed events: `phase_start`, `finding`, `interrupt`, `report_ready`. Human checkpoints = `interrupt` events; resume via `POST /eda/resume`.

## Parallel Build Plan

Lock contracts first (W0), then fan out. All tracks code against shared state + event + API contract.

| Wave | Parallel tracks | Isolated validation |
|------|------|------|
| **W0 Foundation** (serial) | State schema + SSE event schema + API contract | Schema unit tests; freeze as integration contract |
| **W1 Infra** (4 parallel) | A: ingestion (upload+store+loader) · B: 5 deterministic tools · C: sandbox executor · D: frontend panel + SSE client (mock events) | A: fixture CSV · B: crafted fixtures w/ known nulls/dupes/outliers · C: hostile code contained · D: mock event stream |
| **W2 LLM nodes** (3 parallel) | E: Framing+ExpectationModel · F: Evaluator (diff) · G: Synthesizer (report+download) | E: golden objective→priors · F: synthetic (expected,observed) pairs→severity · G: fixture ledger→report |
| **W3 Integration** (serial) | Orchestrator LangGraph wiring · 2 HITL interrupts · Hypothesis Engine (C+orchestrator) | Golden dataset e2e; LangSmith traces |
| **W4 Hardening** | Phase 5 drift (deterministic tool, needs reference profile) · budget tuning · real-dataset e2e | Two-batch drift fixture |

**Parallelism rationale:** 5 mechanical tools (W1-B) + 3 LLM nodes (W2) are mutually independent — each is `(df, state) → findings`. Bulk of work, separate agents simultaneously, fixture-validated, zero integration until W3.

## Risks & Mitigations

1. **Orchestrator quality** (pure-LLM → higher junior-behavior risk) → coverage tracking + budget guard in prompt; LangSmith trace review.
2. **HITL + streaming + interrupts** = fiddly integration → isolate to W3, define `interrupt`/`resume` contract in W0.
3. **Sandbox security** (W1-C) → restricted globals, no fs/net, hard time/mem caps.
4. **Cost/latency** (many LLM calls) → `Budget` guard non-optional (max probes, max hypo iters).

## Touchpoints

- Modify: `eda_agent.py` (→ graph builder), `api/routes/eda.py` (+ upload/resume/stream), `models/workflow_schemas.py` (+ EDA schemas), `client/.../EDAWorkflow.tsx`, `requirements.txt` (+ langsmith)
- New: `ingestion/`, `tools/`, `sandbox/`, `agents/eda/{framing,evaluator,orchestrator,hypothesis,synthesizer}.py`

## Success Criteria

- Upload CSV → agent runs full senior loop → findings stream to UI with severity diffed vs expectation model → 2 human checkpoints → downloadable report.
- Each mechanical tool passes fixture tests with known ground truth.
- Evaluator emits correct severity on synthetic expected/observed pairs.
- LangGraph e2e runs on golden dataset; traces visible in LangSmith.

## Open Questions

- HITL checkpoint UX: blocking modal vs inline panel prompt? (defer to plan/UI phase)
- Sandbox isolation depth: restricted-subprocess sufficient, or container required? (revisit in W1-C)
- Report format priority: markdown first, notebook (.ipynb) export later?
