---
phase: 6
title: "Framing Agent and Expectation Model"
status: pending
priority: P1
effort: "1-2d"
dependencies: [1]
---

# Phase 6: Framing Agent and Expectation Model

## Overview
Phase 0 of the EDA philosophy — the highest-leverage node. An LLM elicits objective/grain/provenance and builds an `ExpectationModel` (priors) BEFORE heavy analysis. This is what lets the agent get "surprised" later. Produces the contract surfaced at human checkpoint 1.

## Requirements
- Functional: given dataset light-profile (column names, dtypes, sample rows from first-contact) + user-stated objective, produce a populated `ExpectationModel` + objective/grain/provenance.
- Non-functional: priors must be concrete (expected null rates, ranges, valid categories, row magnitude) so the Evaluator has something to diff against.

## Architecture
`server/app/agents/eda/framing.py`:
- Node signature is `framing(state, config)` — read `llm = config["configurable"]["llm"]` (Phase 1 injection contract); do NOT take `llm` positionally (LangGraph passes state+config only).
- `build_framing(llm, dataset_meta, sample, user_objective) -> dict` returning `{objective, grain, provenance, expectations: ExpectationModel}`.
- Structured output: bind the LLM to `ExpectationModel` (`.with_structured_output`). **Red-team High:** the model uses list-of-objects (`list[ColumnRange]`, `list[ColumnDtype]`, …) — NOT open `dict[str, tuple]`/`dict[str, list]` — because Gemini's structured-output schema subset rejects `additionalProperties`/`prefixItems`. Add a **3-provider smoke test (openai/anthropic/google)** here, not deferred to Phase 11 — the "provider-agnostic" claim must be verified.
- **Prompt injection (High):** column names + sample rows are UNTRUSTED. Wrap all dataset-derived strings in explicit delimiters framed "data, never instructions"; strip control chars/newlines from column names before prompting. Same discipline carries to Phase 10 probe generation.

### Guard G_frame (truth: validate before trusting)
- **PII redaction (heuristic, before rows leave the boundary):** before ANY sample rows enter an LLM prompt, redact common PII via regex/pattern — emails, phone numbers, SSNs, credit-card numbers. Light heuristic for v1 (note: NER-based engine like Presidio is a later upgrade). Redaction happens in framing AND anywhere else data samples reach an LLM (carry to Phase 10).
- **Schema sanity-check / type compatibility:** validate the LLM's returned `ExpectationModel` against the actual light-profile before trusting it — e.g. every `expected_dtype` names a real column, ranges are numeric for numeric columns, no hallucinated columns. Reject + re-prompt (bounded retries) on mismatch rather than feeding a bogus expectation model to the Evaluator.
- A light profile (cheap pandas: dtypes + sample + cardinality) feeds the prompt — reuse first-contact observation, do NOT run full battery here.
- Prompt encodes the senior framing questions from `EDA_ideal.md` Phase 0.

This node writes `expectations`, `objective`, `grain`, `provenance` into `EDAState`. The graph then interrupts for human contract confirmation (interrupt wiring owned by Phase 9; this phase exposes the pure function).

## Related Code Files
- Create: `server/app/agents/eda/__init__.py`, `server/app/agents/eda/framing.py`
- Create: `server/tests/agents/test_framing.py`

## Implementation Steps
1. Define the framing prompt (objective→grain→provenance→priors) per Phase 0 doc.
2. `build_framing` with `.with_structured_output(ExpectationModel)` for the priors portion.
3. Accept user objective (from upload/run request) + light profile; return the dict.
4. Test with a golden dataset description: assert ExpectationModel fields populated + plausible (mock LLM or recorded response to keep test deterministic/offline).

## Success Criteria
- [ ] Returns a fully-populated `ExpectationModel` (no empty required fields).
- [ ] Objective/grain/provenance captured.
- [ ] Test passes offline (mocked/recorded LLM).
- [ ] Output validates against Phase 1 schema.
- [ ] G_frame: PII (emails/phones/SSN/credit-cards) redacted from sample rows before they enter any prompt; a hallucinated/mismatched ExpectationModel is rejected + re-prompted (bounded).

## Risk Assessment
Risk: vague priors → Evaluator can't diff → junior behavior. Mitigation: structured output forces concrete fields; prompt demands numeric ranges/rates. Risk: LLM hallucinates priors with no data basis. Mitigation: feed it the light profile so priors are grounded in actual columns/samples.
