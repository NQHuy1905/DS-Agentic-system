---
title: "EDA Agentic Workflow"
description: "Completed 2026-07-01 — all 11 phases shipped, 237 tests passing."
status: completed
priority: P2
branch: "master"
tags: []
blockedBy: []
blocks: []
created: "2026-06-30T04:54:53.207Z"
createdBy: "ck:plan"
source: skill
---

# EDA Agentic Workflow

## Overview

Senior-grade, hypothesis-driven EDA agent built as a LangGraph state machine. Carries an expectation model (priors), runs deterministic profiling passes + adaptive LLM-driven probes, diffs observed-vs-expected to emit severity-ranked findings, streams them to the UI (SSE), and synthesizes a downloadable report. Pure-LLM orchestrator routes every probe, bounded by coverage tracking + budget guard.

Source design: `plans/reports/eda-agentic-workflow-architecture-brainstorm-report.md`

## Delivered (2026-07-01)

All 11 phases shipped to `master`; **237 tests passing**. Architecture doc:
`docs/system-architecture.md`; changelog: `docs/project-changelog.md`.

Key deviations from the original plan (all deliberate, verified):
- Prompt externalization added mid-build — every node's prompt loads from
  `server/config/prompts/{node}.yaml` via an injection-safe loader (user request).
- tool-runner + evaluator fused into one graph node (frozen state carries no
  transient observation channel).
- Human checkpoints implemented as static `interrupt_before` no-op gates
  (`contract_gate`/`review_gate`) — langgraph 0.2.45 has no dynamic `interrupt()`.
- Budget/severity thresholds left at defaults pending real live-run tuning.

## Parallel Build Waves

Lock contracts in Phase 1, then fan out. Phases within a wave have disjoint file ownership and run concurrently.

| Wave | Phases | Runs concurrently? |
|------|--------|--------------------|
| W0 Foundation | 1 | Serial — blocks everything |
| W1 Infra | 2, 3, 4, 5 | Yes — 4 parallel agents |
| W2 LLM leaf nodes | 6, 7, 8 | Yes — 3 parallel agents |
| W3 Integration | 9, then 10 | 9 first, then 10 |
| W4 Hardening | 11 | Serial — final |

## Phases

| Phase | Name | Wave | Status |
|-------|------|------|--------|
| 1 | [Foundation Contracts](./phase-01-foundation-contracts.md) | W0 | ✅ Done |
| 2 | [Data Ingestion](./phase-02-data-ingestion.md) | W1 | ✅ Done |
| 3 | [Mechanical Profiling Tools](./phase-03-mechanical-profiling-tools.md) | W1 | ✅ Done |
| 4 | [Sandbox Executor](./phase-04-sandbox-executor.md) | W1 | ✅ Done |
| 5 | [Frontend EDA Panel](./phase-05-frontend-eda-panel.md) | W1 | ✅ Done |
| 6 | [Framing Agent and Expectation Model](./phase-06-framing-agent-and-expectation-model.md) | W2 | ✅ Done |
| 7 | [Evaluator](./phase-07-evaluator.md) | W2 | ✅ Done |
| 8 | [Synthesizer](./phase-08-synthesizer.md) | W2 | ✅ Done |
| 9 | [Orchestrator and Graph Wiring](./phase-09-orchestrator-and-graph-wiring.md) | W3 | ✅ Done |
| 10 | [Hypothesis Engine](./phase-10-hypothesis-engine.md) | W3 | ✅ Done |
| 11 | [Integration Hardening](./phase-11-integration-hardening.md) | W4 | ✅ Done |

## File Ownership (parallel-safe boundaries)

| Phase | Owns (exclusive write) |
|-------|------------------------|
| 1 | `server/app/models/eda_schemas.py`, `server/app/models/eda_events.py`, `server/requirements.txt` (seeds all new deps) |
| 2 | `server/app/ingestion/**` |
| 3 | `server/app/tools/**` + `server/tests/tools/**` |
| 4 | `server/app/sandbox/**` + `server/tests/sandbox/**` |
| 5 | `client/src/components/workflows/eda/**`, `client/src/services/eda-*.ts` |
| 6 | `server/app/agents/eda/framing.py` |
| 7 | `server/app/agents/eda/evaluator.py` |
| 8 | `server/app/agents/eda/synthesizer.py` |
| 9 | `server/app/agents/eda/orchestrator.py`, `server/app/agents/eda/graph.py`, `server/app/agents/eda_agent.py`, `server/app/api/routes/eda.py` |
| 10 | `server/app/agents/eda/hypothesis.py` |
| 11 | cross-cutting: e2e tests `server/tests/e2e/**`, LangSmith env config, drift tuning |

**Note:** Phase 1 seeds all empty package `__init__.py` markers (`ingestion`, `tools`, `sandbox`, `agents/eda`, test dirs) so W1/W2 phases create only their content files — no race on package creation.

## Phase Dependencies

```
1 ──┬─> 2 ─┐
    ├─> 3 ─┼─> 9 ─> 10 ─> 11
    ├─> 6 ─┤        ↑
    ├─> 7 ─┤        │
    ├─> 8 ─┘        │
    ├─> 4 ──────────┘   (sandbox feeds the hypothesis node, not graph wiring)
    └─> 5            (frontend: contract-only dep on 1; not a build-blocker for 9)
```
- 2,3,4,5,6,7,8 all depend only on **1**.
- 9 depends on **2,3,6,7,8** (graph + routes + HITL streaming). NOT 5 — frontend shares the Phase 1 contract but is not a runtime build-blocker (red-team: removed the false coupling).
- 10 depends on **4,9** (sandbox + orchestrator loop).
- 11 depends on **9,10** (e2e + drift tuning + tracing).

## Key Constraints

- Stack locked: FastAPI + LangGraph + React 19/Vite; Python env conda `research`.
- Pure-LLM orchestrator (user decision) — not constrained routing.
- Hybrid execution: deterministic tools (P1–5 of EDA) + sandboxed LLM code (Phase 6 deep-dives).
- LangSmith optional/env-gated.

## Guardrail Refinement

### Session — 2026-06-30 (post red-team)
Refined `EDA_ideal_diagram.mmd` adds a guardrail overlay: 3 safety guards (🔴, on Python/execution boxes — prevent harm) + 3 truthfulness guards (🔵, on LLM-judgment boxes — prevent silent false assertions) + 2 blocking human HARD GATES. Distributed into owning phases (a guard belongs to the box it guards); no new phases.

| Guard | Type | Phase | What it adds beyond red-team |
|-------|------|-------|------------------------------|
| G_frame | 🔵 truth | 6 | **PII redaction** (heuristic regex) before rows reach LLM; expectation-model schema sanity-check + bounded re-prompt |
| G_mech | 🔴 safety | 3 | **Context/cost caps**: summaries-only, top-k truncation, output-size cap, seeded sampling (red-team only had memory sampling) |
| G_orch | 🔴 safety | 9 | **Circuit breaker on repeated calls** (loop detection) on top of the deterministic budget counter |
| G_sandbox | 🔴 safety | 4 | Already satisfied by red-team hardening — labeled as the densest guard; precondition for real data |
| G_eval | 🔵 truth | 7 + 1 | **Grounding keystone**: `Finding.evidence_ref` REQUIRED (hard) — evaluator cannot emit an ungrounded finding |
| G_synth | 🔵 truth | 8 | **Claim-tracing** (soft flag, not drop), **propose-never-execute**, destructive-op sign-off at human gate |

**Grounding chain (keystone):** `tool output → Observation.id → Finding.evidence_ref → Ledger → traceable claim in report`. Hard at the Finding (required field), soft at the report (unverified prose flagged, not dropped) — per user decision.

**Sequencing:** safety guards (G_sandbox/G_orch/G_mech) are preconditions before P11 real-data runs — cannot skip. Truthfulness guards ship incrementally; land G_eval grounding first.

**Decisions:** grounding = hard-findings/soft-report; PII = heuristic regex (Presidio deferred); both human checkpoints = blocking hard gates.

## Red Team Review

### Session — 2026-06-30
**Findings:** 33 raw → 15 deduped (4 reviewers: Security Adversary, Failure Mode Analyst, Assumption Destroyer, Scope & Complexity Critic). 3 contract bugs empirically verified on conda `research` / Python 3.9.21.
**Severity:** 9 Critical, 5 High, 1 Medium accepted/applied. Scope-cut findings **rejected** per user (keep full scope). Checkpointer ambiguity **resolved → SQLite** per user.

| # | Finding | Sev | Disposition | Applied To |
|---|---------|-----|-------------|------------|
| 1 | `X \| None` won't import on Py 3.9 (verified) | Critical | Accept | Phase 1 (`from __future__ import annotations`) |
| 2 | No LangGraph reducers — ledger overwrites, findings lost (verified) | Critical | Accept | Phase 1 (`Annotated[list, operator.add]`) |
| 3 | No LLM-injection path into nodes; api_key would leak into state/checkpoint | Critical | Accept | Phase 1 (`config["configurable"]["llm"]`); Phases 6/7/8/9/10 sigs |
| 4 | Tool-observation dict unpinned — parallel waves diverge, empty report | Critical | Accept | Phase 1 (typed `Observation`); Phase 3/7 |
| 5 | Missing `python-multipart` (+checkpointer/sse deps) — upload 500s | Critical | Accept | Phase 1 (seed deps) |
| 6 | Sandbox trivially escapable via allowlisted pandas (read_pickle RCE, file/URL I/O, no-network unenforced) | Critical | Accept | Phase 4 (strip readers, netns, not a boundary); Phase 10 |
| 7 | Client disconnect leaks graph task + unbounded queue + runaway LLM spend | Critical | Accept | Phase 9 (disconnect cancel, bounded buffer, task registry) |
| 8 | TTL cleanup deletes dataset under paused run | Critical | Accept | Phase 2 (lease/refcount); Phase 9 (lease on run) |
| 9 | SSE breaks on resume — no event buffer / Last-Event-ID | Critical | Accept | Phase 1 (event `id`); Phase 9 (durable buffer); Phase 5 (cursor) |
| 10 | Path traversal on dataset/report refs | High | Accept | Phase 2 + Phase 9 (uuid-validate + commonpath) |
| 11 | Decompression bomb / no magic-byte check | High | Accept | Phase 2 (magic bytes, row×col ceiling) |
| 12 | Prompt injection via dataset content steers probe code | High | Accept | Phase 6 + Phase 10 (delimit/sanitize) |
| 13 | api_key plaintext in body, persisted in checkpoint, traced unredacted, undefined on resume | High | Accept | Phase 1 (SecretStr) + Phase 9 (run-keyed store) + Phase 11 (mask) |
| 14 | `with_structured_output` breaks on Gemini (open dicts/tuples) | High | Accept | Phase 1 (list-of-objects) + Phase 6 (3-provider smoke test) |
| 15 | Hardening cluster: budget not hard-enforced; subprocess orphans + mem_mb false-kill; `run_code` sig contradiction; dead chase edge; no auth/global caps; langsmith unpin | High/Med | Accept | Phases 4, 9, 11 |
| — | Defer P4+P10 (sandbox+hypothesis) to v2 | High | **Reject** | User chose full scope |
| — | Drop `drift.py` (dead on single-CSV) | High | **Reject** | User chose full scope |
| — | Collapse parallel waves (solo build) | Med | **Reject** | User confirmed multi-agent parallel |
| — | MemorySaver instead of SQLite | Med | **Reject** | User chose SQLite persistent |

### Whole-Plan Consistency Sweep
- Phase 9 `dependencies` corrected `[2,3,5,6,7,8]` → `[2,3,6,7,8]` (frontend P5 is contract-only, not a build-blocker); plan.md dependency graph updated to match.
- `run_code` signature unified to `(code, dataset_ref, ...)` across Phase 4 + Phase 10 (deleted the `(code, df)` form).
- Observation shape now typed in Phase 1 + referenced by Phase 3 (producer) and Phase 7 (consumer) — no divergent dict.
- LLM-injection contract (`config["configurable"]["llm"]`) propagated to Phases 6/7/8/9/10 node signatures.
- Checkpointer resolved to SQLite everywhere (Phase 1 dep seed + Phase 9 wiring); MemorySaver references removed.
- `langsmith` removed from Phase 1 dep seed (transitive); Phase 11 updated to match.
- No unresolved contradictions remain.

## Dependencies

<!-- No cross-plan dependencies: this is the first plan in the project. -->

