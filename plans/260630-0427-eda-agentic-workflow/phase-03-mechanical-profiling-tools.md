---
phase: 3
title: "Mechanical Profiling Tools"
status: pending
priority: P1
effort: "2-3d"
dependencies: [1]
---

# Phase 3: Mechanical Profiling Tools

## Overview
Five deterministic pandas profiling functions — the "Mechanical Tools" of the diagram. Each takes a DataFrame + EDAState and returns raw observations (NOT findings — the Evaluator turns observations into severity-ranked findings by diffing vs expectations). Pure, side-effect-free, individually fixture-testable.

## Requirements
- Functional: `first_contact`, `structural`, `univariate`, `bivariate`, `drift` — each returns a typed `Observation` (with `id`) per the Phase 1 contract.
- Non-functional: deterministic (same input → same output), no LLM calls, no network, handles wide/large frames without blowing memory (sample where needed).

## Guard G_mech (safety: context + determinism)
This is a **safety guard** — it bounds the context/cost blowup that lets a 50k-cardinality column dump 50k categories into the LLM, and makes runs reproducible. Each tool MUST:
- **Summaries only** — emit aggregates, never raw row dumps; observations are bounded structured values, not the data itself.
- **Sample N rows / seeded sampling** — any sampling uses a fixed seed (reproducible); record the seed in the Observation.
- **Top-k truncation** — categorical frequencies, rare-category lists, outlier lists capped at top-k with an "N more" count, never the full list.
- **Output-size cap** — hard ceiling on each Observation's serialized size; truncate + flag if exceeded.
- **Pinned versions** — pandas/numpy pinned (Phase 1 requirements) so stats are stable across runs.
Each Observation carries an `id` (anchors `Finding.evidence_ref`, the grounding chain).

## Architecture
New package `server/app/tools/`. Each tool: `run(df, state) -> dict` returning structured observations the Evaluator consumes. These are registered in a `TOOL_REGISTRY` so the Phase 9 orchestrator can call them by name.

- `first_contact.py` — shape, dtypes, head/tail/random sample, parse-tell detection (numeric-looking strings, mixed date formats).
- `structural.py` — missingness (count + pattern: per-column, per-row, correlation of nullness), full-row + key-level duplicates (test grain from `state.grain`), validity (ranges, negative ages, future dates, category-set violations, string hygiene: whitespace/casing/unicode-lookalikes).
- `univariate.py` — numeric: dist shape, center, spread, skew, outliers (IQR/z); categorical: cardinality, frequency, rare cats, near-duplicate variants ("USA"/"U.S.A."); datetime: range, granularity, gaps.
- `bivariate.py` — feature-feature correlation/collinearity, feature-target relationships, grouped aggregations across segments.
- `drift.py` — compare current df distribution vs a reference profile (passed in state or prior batch); PSI/KS per column. Needs a reference; no-op + flag if none.
- `registry.py` — `TOOL_REGISTRY: dict[str, Callable]` + `run_tool(name, df, state)`.

**Observation shape is PINNED by Phase 1** (typed `StructuralObs`/`UnivariateObs`/etc. with a `tool` discriminator) — red-team Critical: an untyped dict here would let Phase 7's evaluator (a separate parallel agent) consume a mismatched shape and silently emit zero findings at integration. Tools return the Phase 1 pydantic `Observation` models, NOT free-form dicts. Example `StructuralObs`:
```python
StructuralObs(
  tool="structural",
  missingness=[ColMissingness(column="col_a", null_rate=0.12, pattern="row-concentrated")],
  duplicates=Duplicates(full_row=4, key_level=[KeyDup(key="user_id", count=12)]),
  validity=[ColValidity(column="age", negatives=3, future_dates=0)],
)
```
If a tool needs an observation field not yet in the Phase 1 model, that is a coordinated Phase 1 contract change — not a local dict key.

## Related Code Files
- Create: `server/app/tools/{__init__,first_contact,structural,univariate,bivariate,drift,registry}.py`
- Create: `server/tests/tools/test_*.py` + `server/tests/tools/fixtures/*.csv`

## Implementation Steps
1. Define the observation dict conventions (document at top of `registry.py`).
2. Implement each tool as a pure `run(df, state) -> dict`.
3. Build `registry.py` with `TOOL_REGISTRY` + `run_tool`.
4. Craft fixture CSVs with KNOWN ground truth: a file with exactly N nulls in a pattern, K duplicate keys, M outliers, mixed-format dates, "USA"/"U.S.A." variants.
5. Per-tool tests assert observations match the planted ground truth exactly.
6. Large-frame guard: sampling path for univariate/bivariate above a row threshold.

## Success Criteria
- [ ] Each of 5 tools returns correct observations on its fixture with planted ground truth.
- [ ] `run_tool("structural", df, state)` dispatches correctly.
- [ ] Tools are deterministic + import-clean under conda `research`.
- [ ] Drift no-ops gracefully when no reference profile present.
- [ ] G_mech: high-cardinality column yields top-k-truncated output (not full list); each Observation respects the output-size cap and carries a seed + `id`.

## Risk Assessment
Risk: scope creep per tool (infinite stats). Mitigation: implement exactly the checks named in `EDA_ideal.md` Phases 1–5, nothing more (YAGNI). Risk: memory on wide frames. Mitigation: sampling threshold. This phase is the most parallelizable — could be split across sub-agents per tool if needed.
