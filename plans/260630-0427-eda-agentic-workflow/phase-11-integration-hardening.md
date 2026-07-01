---
phase: 11
title: "Integration Hardening"
status: completed
priority: P2
effort: "2d"
dependencies: [9, 10]
---

# Phase 11: Integration Hardening

## Overview
End-to-end validation on real datasets, enable LangSmith tracing, tune the drift tool + orchestrator budget/thresholds, and close the loop on the two human checkpoints under realistic load.

## Requirements
- Functional: full run on 2-3 real-world CSVs; LangSmith traces visible; drift tool exercised with a two-batch scenario.
- Non-functional: acceptable latency/cost per run documented; graceful failure on malformed data mid-run.

## Architecture
- Enable LangSmith via env (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`) — gated so absence = no-op. **Red-team:** `langsmith` is NOT in requirements (it's transitive via langchain-core==0.3.15; an unpinned add risks version skew) — tracing works on the transitive dep. **Configure LangSmith input/output masking** so the user's `api_key` and uploaded-data samples embedded in prompts are NOT shipped to / persisted in traces. Verify the SQLite checkpoint + ErrorEvents never serialize the key (`SecretStr`, excluded from state per Phase 1).
- Drift (`tools/drift.py`) tuning: supply a reference profile path; validate PSI/KS on a two-batch fixture.
- Budget/threshold tuning: adjust orchestrator `Budget` defaults + Evaluator severity constants from observed real-run behavior.
- Error resilience: malformed-row mid-run, sandbox timeout mid-chase, LLM API failure → surfaced as `ErrorEvent`, run ends cleanly.

## Related Code Files
- Create: `server/tests/e2e/test_full_eda_run.py`, `server/.env.example` additions (LangSmith vars)
- Modify: env config only (do not re-touch phase-owned modules except via coordinated tuning constants)

## Implementation Steps
1. Add LangSmith env vars to `server/.env.example`; verify traces appear when key set, no-op when unset.
2. E2E test: real CSV → full run → assert report produced, findings sane, both interrupts handled.
3. Two-batch drift fixture → assert drift tool flags distribution shift.
4. Tune Budget defaults + severity thresholds from real-run observation; document chosen values + rationale.
5. Inject failure scenarios (malformed row, sandbox timeout, API error) → assert `ErrorEvent` + clean termination.
6. Document per-run latency/cost ballpark.

## Success Criteria
- [ ] Full run completes on 2-3 real datasets producing a sensible report.
- [ ] LangSmith traces visible with key; clean no-op without.
- [ ] Drift tool flags shift on two-batch fixture.
- [ ] Failure injections end with `ErrorEvent`, no server crash.
- [ ] Budget/thresholds tuned + documented.

## Risk Assessment
Risk: real datasets expose tool/evaluator gaps. Mitigation: this phase is the catch-net; fixes route back to the owning phase's module. Risk: cost surprises at scale. Mitigation: budget caps already enforced; document observed cost so users set expectations.

## Open Questions (carry from design doc)
- HITL checkpoint UX: blocking modal vs inline (frontend, revisit post-demo).
- Sandbox depth: subprocess vs container for any non-local deployment.
- `.ipynb` report export: defer unless requested.
