---
phase: 10
title: "Hypothesis Engine"
status: completed
priority: P2
effort: "2-3d"
dependencies: [4, 9]
---

# Phase 10: Hypothesis Engine

## Overview
Phase 6 of the EDA philosophy — the adaptive loop where most judgment lives. For a chase-worthy surprise, generate candidate causes, design a targeted probe (LLM-generated code), run it in the sandbox, and either confirm a root cause or try the next hypothesis. The hardest, most open-ended node — built last, bounded by budget.

## Requirements
- Functional: `chase(llm, surprise, state, sandbox) -> Finding update` — loop {hypothesize → probe → verdict} until confirmed or `max_hypo_iters` hit.
- Non-functional: every probe runs in the Phase 4 sandbox; bounded iterations; records root cause + downstream impact into ledger.

## Architecture
`server/app/agents/eda/hypothesis.py` (node signature `hypothesis(state, config)`; `llm = config["configurable"]["llm"]`):
- `hypothesize(llm, surprise, state) -> list[cause]` — generate candidate causes, traced to source process (upstream join, default value, timezone bug — per doc).
- `design_probe(llm, cause, state) -> code` — emit pandas code to test the cause. **Red-team High:** `state` carries data-derived strings (column names, sampled values) that are UNTRUSTED and could carry prompt-injection steering the LLM to emit a malicious probe (e.g. `pd.read_pickle("http://…")`). Delimit/sanitize all data-derived strings in the prompt AND apply the same G_frame PII redaction (heuristic regex) to any sample values that reach the LLM here (same discipline as Phase 6). Do NOT rely on the sandbox as the sole barrier — the Phase 4 hardening (stripped file/URL readers, no-network) must be in place first, since code generation and containment are breached by the same untrusted input.
- Run via `sandbox.run_code(code, dataset_ref)` (the pinned ref signature); interpret result → `verdict(confirmed?)`.
- On confirm: update the originating `Finding` with `root_cause` + `decision`; mark `Surprise.chased`. On exhaust: record "unexplained" + move on.
- Plugged into the graph as the node the orchestrator routes to when it picks "chase a surprise". Decrement `budget.hypo_spent`.

## Related Code Files
- Create: `server/app/agents/eda/hypothesis.py`
- Create: `server/tests/agents/test_hypothesis.py`
- Modify: `server/app/agents/eda/graph.py` (replace the Phase 9 stub edge with the real hypothesis node) — coordinate: Phase 9 leaves a clearly-marked stub hook for this.

## Implementation Steps
1. `hypothesize`: prompt for candidate causes traced to source.
2. `design_probe`: prompt → pandas code targeting one cause.
3. Loop: probe via sandbox → interpret → verdict; iterate to `max_hypo_iters`.
4. On confirm, write root_cause+decision back to the Finding; mark surprise chased.
5. Wire real node into `graph.py` (replace stub).
6. Test: a fixture surprise with a known cause (e.g., nulls in col X only when col Y == value) → assert the loop confirms it within budget (mock LLM to emit the known probe; real sandbox executes it).

## Success Criteria
- [ ] Confirms a planted root cause on a fixture within `max_hypo_iters`.
- [ ] All probes execute in sandbox (no direct exec).
- [ ] Exhausted hypotheses recorded as "unexplained", loop terminates.
- [ ] Budget decrements; orchestrator regains control after chase.

## Risk Assessment
Risk: open-ended loop burns budget/cost. Mitigation: `max_hypo_iters` hard cap; orchestrator decides whether a surprise is worth chasing at all. Risk: LLM probe code is wrong/unsafe. Mitigation: sandbox contains it; bad probes just yield no confirmation. Risk: hardest node — may slip. Mitigation: system is already useful without it (P9 delivers full non-adaptive loop); this is additive.
