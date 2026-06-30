---
phase: 8
title: "Synthesizer"
status: pending
priority: P1
effort: "1-2d"
dependencies: [1]
---

# Phase 8: Synthesizer

## Overview
Phase 7 of the EDA philosophy — captures findings into a reproducible deliverable: data dictionary, issue log (with severity + root cause + decision), and pipeline recommendations. Produces the downloadable report surfaced at human checkpoint 2.

## Requirements
- Functional: `synthesize(llm, state) -> {markdown: str, report_ref: str}` from the populated `ledger` + `expectations`.
- Non-functional: report is self-contained + re-runnable narrative; deterministic structure (sections fixed), LLM fills prose.

## Guard G_synth (truth: claims trace to evidence)
- **Claim grounding (soft report — user decision):** every substantive claim in the report should trace to a Ledger entry (`Finding.id` → its `evidence_ref` → Observation). Claims that don't trace are **flagged "unverified / low-confidence", NOT dropped** (soft). The deterministic tables (issue log, data dictionary) are built directly from `ledger` so they're grounded by construction; the LLM prose is what gets the soft flag.
- **Propose, never execute:** the Synthesizer recommends transforms/drops/imputations as TEXT only — it never runs them. The report is advisory; no data mutation happens here.
- **Destructive ops need sign-off:** any recommendation that would drop/overwrite data is surfaced at the Phase 9 human-review HARD GATE for explicit sign-off before it could ever be acted on downstream.

## Architecture
`server/app/agents/eda/synthesizer.py`:
- Node signature `synthesizer(state, config)` — read `llm = config["configurable"]["llm"]` (Phase 1 injection contract); not positional.
- Reads the FULL accumulated `state.ledger` — correctness depends on Phase 1 reducers (`Annotated[list, operator.add]`); without them the ledger would hold only the last node's findings.
- Deterministic report skeleton (sections: Objective/Grain, Data Dictionary, Issue Log by severity, Decisions & Rationale, Pipeline Recommendations, Caveats).
- LLM writes the narrative per section from `ledger` + `expectations`; tables (data dictionary, issue log) generated deterministically from findings.
- Persist markdown to storage (reuse ingestion storage dir) → `report_ref`; expose via a download route (route owned by Phase 9).
- Optional `.ipynb` export deferred (open question in design doc) — out of scope this phase.

## Related Code Files
- Create: `server/app/agents/eda/synthesizer.py`
- Create: `server/tests/agents/test_synthesizer.py`

## Implementation Steps
1. Define the fixed report skeleton + deterministic table builders (data dictionary from expectations+observations, issue log from ledger sorted by severity).
2. LLM narrative pass per section (mockable).
3. Persist markdown → `report_ref`; return `{markdown, report_ref}`.
4. Tests: feed a fixture ledger → assert all sections present, issue log ordered by severity, every critical finding appears, tables well-formed (offline/mocked LLM).

## Success Criteria
- [ ] Report contains all fixed sections.
- [ ] Issue log lists every finding, ordered by severity.
- [ ] Data dictionary derived from expectations + observations.
- [ ] `report_ref` persisted + retrievable.
- [ ] G_synth: deterministic tables trace to ledger entries; ungrounded LLM prose is flagged "unverified" (soft), not dropped; report contains zero executed mutations (propose-only).
- [ ] Test passes offline.

## Risk Assessment
Risk: LLM omits/invents findings. Mitigation: tables built deterministically from `ledger`, LLM only writes prose around them — it cannot drop a finding. Risk: report bloat. Mitigation: severity-first ordering; info-level findings collapsed.
