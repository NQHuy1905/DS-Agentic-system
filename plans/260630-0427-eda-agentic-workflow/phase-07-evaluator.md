---
phase: 7
title: "Evaluator"
status: completed
priority: P1
effort: "1-2d"
dependencies: [1]
---

# Phase 7: Evaluator

## Overview
Turns raw tool observations into severity-ranked `Finding`s by diffing observed-vs-expected against the `ExpectationModel`, and flags surprises worth chasing. This is the "expected vs observed" mechanism the design doc calls the highest-leverage choice.

## Requirements
- Functional: `evaluate(observations, expectations, state) -> list[Finding] + list[Surprise]`. Severity from the gap magnitude + downstream impact. Hybrid per diagram: **Python computes the deltas, LLM only scores severity** for ambiguous cases.
- Non-functional: deterministic where possible (rule-based diffs); LLM only for ambiguous severity/impact judgment, not for the mechanical diff.

## Guard G_eval (truth: grounding — the keystone)
**Hard enforcement (user decision):** every `Finding` is constructed with a required `evidence_ref` pointing to the `Observation.id` that produced it (Phase 1 contract). The evaluator **literally cannot emit an ungrounded finding** — Pydantic rejects construction without `evidence_ref`. This collapses most of the silent-failure surface: no finding exists that isn't traceable to a specific tool output. Each Finding's `id` is also what `Surprise.finding_id` and the Synthesizer's claim-tracing key off. If implementing guards incrementally, this is the one to land first.

## Architecture
`server/app/agents/eda/evaluator.py`:
- Node signature `evaluator(state, config)`; if the optional LLM annotation pass is used, read `llm = config["configurable"]["llm"]` (Phase 1 injection contract).
- Consumes the **typed `Observation` models pinned in Phase 1** (not free-form dicts) — field names are contract-guaranteed to match Phase 3's output, closing the silent-empty-report risk.
- Mostly **rule-based**: compare observation fields to expectation fields (null_rate observed vs prior, dtype observed vs schema, range violations vs ranges, cardinality blowups, etc.) → emit Finding with severity per thresholds.
- Optional LLM pass for descriptions/root-cause hypotheses + borderline severity (kept thin to stay testable/cheap).
- Emit `Surprise` for any finding above a "worth chasing" bar (critical, or unexplained warn) → feeds orchestrator's Phase-6 decision.

Severity rubric (deterministic core):
- `critical`: grain violation, schema/dtype mismatch on key col, validity breach (negative age), target leakage signal.
- `warn`: null rate >2× prior, new rare categories, mild range drift, collinearity.
- `info`: within-prior observations worth recording.

## Related Code Files
- Create: `server/app/agents/eda/evaluator.py`
- Create: `server/tests/agents/test_evaluator.py`

## Implementation Steps
1. Map each tool's observation fields → comparison rules vs ExpectationModel.
2. Implement deterministic severity rubric → `Finding`s.
3. Surprise extraction (which findings spawn "why is this weird?").
4. Optional thin LLM hook for description/root-cause (mockable, off by default in tests).
5. Tests: synthetic (expectation, observation) pairs with known gaps → assert exact severity + surprise emission. E.g., prior null_rate 0.01 vs observed 0.40 → `warn`/`critical`; dtype int expected, object observed → `critical`.

## Success Criteria
- [ ] Correct severity on a matrix of synthetic expected/observed pairs.
- [ ] **Every emitted Finding carries a valid `evidence_ref` to an existing Observation.id; constructing one without it raises (grounding hard-enforced).**
- [ ] Surprises emitted only for chase-worthy findings.
- [ ] Deterministic core passes offline (no LLM needed for the rubric).
- [ ] Findings validate against Phase 1 schema.

## Risk Assessment
Risk: over-reliance on LLM makes evaluation non-reproducible. Mitigation: deterministic rubric is the spine; LLM only annotates. Risk: thresholds arbitrary. Mitigation: thresholds are explicit constants, tunable in Phase 11, documented inline.
