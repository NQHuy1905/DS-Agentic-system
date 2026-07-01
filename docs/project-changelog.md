# Project Changelog

## 2026-07-01 — EDA Agentic Workflow (Phases 1–11) complete

The full exploratory-data-analysis agent is built and shipped to `master`.
**237 tests passing.** Plan: `plans/260630-0427-eda-agentic-workflow/`.
Architecture: `docs/system-architecture.md`.

### Added
- **Foundation contracts** — `EDAState`, typed `Observation`s, `EDAEvent`s, LLM-injection
  convention, reducers (Phase 1).
- **Ingestion** — multipart upload, magic-byte validation, path-traversal + decompression
  guards, dataset lease/TTL storage (Phase 2).
- **Mechanical tools** — first_contact, structural, univariate, bivariate, drift + registry,
  with output/cost caps and seeded sampling (Phase 3).
- **Sandbox executor** — subprocess isolation, stripped pandas/numpy IO, restricted builtins,
  resource limits (Phase 4).
- **Frontend EDA panel** — upload, live SSE findings feed with severity styling, contract +
  review interrupts, report download (Phase 5).
- **Framing / Evaluator / Synthesizer** — expectation-model priors, observed-vs-expected
  severity rubric with hard grounding, deterministic ledger-backed report (Phases 6–8).
- **Prompt externalization** — per-node YAML prompts under `server/config/prompts/` via an
  injection-safe loader.
- **Orchestrator + graph + agent + routes** — pure-LLM planner bounded by deterministic
  guards, StateGraph with two blocking human checkpoints, SQLite checkpointer, EDAService
  with durable per-run SSE buffer, run/stream/resume/report routes (Phase 9).
- **Hypothesis engine** — adaptive chase loop with sandboxed pandas probes and root-cause
  write-back (Phase 10).
- **Integration hardening** — gated LangSmith tracing with data masking, error-resilience,
  secret-hygiene, and a fixture-driven e2e run over real CSVs (Phase 11).

### Fixed
- `LLMConfig` now accepts the client's camelCase `apiKey` (was 422 on `/eda/run`).
- Server logging: `conda run --no-capture-output` + `python -u` + `logging.basicConfig`
  so failed-run tracebacks reach `logs/server.log` (was empty due to `conda run` buffering).
- Review-gate fixes across phases: SSE disconnect-cancel no longer kills resumable runs;
  numpy-float probe results can no longer fabricate a confirmed root cause; prompt
  delimiter-breakout hardened in framing + hypothesis.

### Notes
- Budget defaults + severity thresholds are starting values pending real live-run tuning.
- e2e runs offline (scripted LLM) on real data shapes; a fully live run needs a provider key.
