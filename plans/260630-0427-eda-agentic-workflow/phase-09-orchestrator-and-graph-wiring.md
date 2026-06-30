---
phase: 9
title: "Orchestrator and Graph Wiring"
status: pending
priority: P1
effort: "3-4d"
dependencies: [2, 3, 6, 7, 8]
---

# Phase 9: Orchestrator and Graph Wiring

## Overview
Assemble all leaf nodes into a LangGraph `StateGraph`, implement the pure-LLM orchestrator (picks next probe each loop), wire the two human-in-the-loop interrupts, and connect the API routes + SSE streaming. This is where the system becomes a running agent.

## Requirements
- Functional: full graph executes framing → [interrupt] → orchestrator loop over tools+evaluator → [interrupt] → synthesizer; routes drive it; events stream via SSE.
- Non-functional: orchestrator bounded by `Budget` + `completed_passes` coverage; resumable via LangGraph checkpointer.

## Architecture
- `server/app/agents/eda/orchestrator.py` — **pure-LLM** planner node `orchestrator(state, config)`: given `expectations + ledger + completed_passes + open_surprises + budget`, the LLM returns `next_action` ∈ {run a named tool, chase a surprise (→hypothesis), synthesize, stop}. Coverage + budget injected into the prompt.
  - **Guard G_orch (safety: loop control)** — enforced in DETERMINISTIC graph code, not the prompt: (a) hard budget counter — when `probes_spent >= max_probes` OR a wall-clock/cost ceiling is hit, the graph forces `synthesize` regardless of `next_action`; (b) per-hypothesis cap (`max_hypo_iters`); (c) **circuit breaker on repeated calls** — detect the orchestrator re-selecting the same tool/probe with no new ledger progress (N identical consecutive actions) and force-advance or stop. Together these bound the pure-LLM router's runaway/junior-loop risk (the red-team's #1 concern) with code, not trust.
- `server/app/agents/eda/graph.py` — `StateGraph(EDAState)`: nodes = framing, orchestrator, tool-runner (dispatches `TOOL_REGISTRY` → typed `Observation`), evaluator, hypothesis, synthesizer.
  - **Checkpointer = SQLite** (`langgraph-checkpoint-sqlite`, dep seeded in Phase 1) — user choice for durable paused runs. Restart recovery: on resume, rehydrate from checkpoint AND rebuild the SSE stream from the persisted ledger (ties to event-buffer below). Document that an in-flight *non-interrupted* run's live task is lost on restart and must be re-driven from its last checkpoint.
  - `interrupt_before` the two human checkpoints — these are **HARD GATES (blocking, not optional)** per the refined diagram: contract confirmation (after framing) and findings review (after synthesis). The graph cannot proceed past either without an explicit human `resume`. Destructive recommendations from G_synth surface for sign-off at the second gate.
  - **Dead-chase-edge fix:** until Phase 10 ships, wire the hypothesis node as a real **no-op that records "chase deferred" and returns control to the orchestrator** — NOT a dead/missing edge. Also constrain the Phase 9 orchestrator's allowed actions to exclude `chase` (or pin the integration-test LLM to a non-chase script) so the pure-LLM router can't route into an unimplemented node and flake the test.
- `server/app/agents/eda_agent.py` — replace stub: build + compile graph; `run(state, config)` + `resume(...)` async generators yielding `EDAEvent`s. **LLM injected via `config["configurable"]["llm"]`** (Phase 1 contract), never via state.
- `server/app/api/routes/eda.py` — replace stub: mount ingestion router; `POST /run`, `GET /stream/{run_id}`, `POST /resume/{run_id}`, `GET /report/{ref}` (uuid-validate + commonpath containment — same path-traversal fix as Phase 2).

### Run lifecycle, streaming & resource bounds (red-team Criticals)
- `POST /run` spawns a **background task** driving the graph into a **durable per-run event buffer** (append-only, keyed by `run_id`, carrying Phase 1 event `id`s) — NOT an ephemeral queue.
- `GET /stream/{run_id}` replays from the buffer honoring `Last-Event-ID`, so reconnect-gap + post-resume events (the whole second half of the run) are delivered. `StreamingResponse` (text/event-stream) via Phase 1 `serialize()`.
- **Client-disconnect handling:** detect via `await request.is_disconnected()` / generator `finally`; on disconnect **cancel or checkpoint-pause the graph task** (don't let the pure-LLM loop run to completion burning tokens with no consumer). Register every run task in a registry so shutdown cancels them. Bounded buffer with a drop/await policy.
- **api_key lifecycle:** held in a short-lived in-memory secret store keyed by `run_id` (so `/resume` can rebuild the LLM); never written to the checkpoint, SSE, logs, or LangSmith; deleted on terminal/abandoned runs.
- **Dataset lease:** call `storage.lease(dataset_ref, run_id)` on run start, `release` on terminal — so Phase 2 TTL cleanup can't delete data under a paused run.
- **Resource caps:** global concurrency semaphore on runs + global subprocess cap (no per-run-only `Budget`); bind server to localhost; upload rate limit. The app currently mounts routers with no auth — document single-user/localhost as an explicit non-goal boundary.

## Related Code Files
- Create: `server/app/agents/eda/orchestrator.py`, `server/app/agents/eda/graph.py`
- Modify: `server/app/agents/eda_agent.py`, `server/app/api/routes/eda.py`

## Implementation Steps
1. `orchestrator.py`: prompt + structured output for `next_action`; budget/coverage injection + decrement.
2. `graph.py`: assemble StateGraph, conditional routing on `next_action`, checkpointer, `interrupt_before` both checkpoints; hypothesis node as a stub edge (filled Phase 10).
3. `eda_agent.py`: compile graph; `run()`/`resume()` async generators yielding events (phase_start, finding, interrupt, report_ready).
4. `routes/eda.py`: mount ingestion router; implement run/stream/resume/report; bridge generator → SSE queue.
5. Integration test: golden dataset → run to first interrupt → resume → run to review interrupt → resume → report. Assert event sequence + ledger populated.

## Success Criteria
- [ ] Graph compiles + runs end-to-end on a golden CSV.
- [ ] Both human interrupts pause + resume correctly via checkpointer.
- [ ] Orchestrator completes standard battery then stops within budget (verify via LangSmith trace / logged decisions).
- [ ] SSE stream delivers typed events to a client.
- [ ] `/report/{ref}` downloads the synthesized markdown.

## Risk Assessment
Risk: pure-LLM orchestrator loops or skips passes (junior behavior) — the accepted trade-off. Mitigation: coverage + budget guard in prompt; hard budget stop; log every decision for trace review. Risk: interrupt/resume + SSE integration is fiddly. Mitigation: this phase is deliberately serial after all leaves exist; test interrupt flow in isolation first with a trivial 1-tool graph.
