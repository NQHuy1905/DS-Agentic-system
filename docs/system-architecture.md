# System Architecture — EDA Agentic Workflow

Senior-grade, hypothesis-driven exploratory data analysis built as a LangGraph
state machine. It carries an expectation model (priors), runs deterministic
profiling passes plus adaptive sandboxed LLM probes, diffs observed-vs-expected
into severity-ranked findings, streams them to the UI over SSE, and synthesizes a
downloadable report. A pure-LLM orchestrator routes each step, bounded by
deterministic budget + circuit-breaker guards.

Stack: FastAPI + LangGraph 0.2.45 + LangChain (server, Python 3.9, conda env
`research`); React 19 + Vite + Tailwind (client). Single-user / localhost is an
explicit non-goal-to-secure boundary (no auth).

## Graph

```
START → framing → [contract interrupt] → orchestrator ⇄ tool_runner
                                              │  ⇅ hypothesis (chase)
                                              ▼
                                        [review interrupt] → synthesizer → END
```

Nodes (`server/app/agents/eda/graph.py`):
- **framing** — LLM builds the `ExpectationModel` (priors) + objective/grain/provenance.
- **contract_gate / review_gate** — no-op nodes used as static `interrupt_before`
  anchors (the two blocking human checkpoints). langgraph 0.2.45 has no dynamic
  `interrupt()`; resume = `aupdate_state` (apply edits) + re-invoke with `None`.
- **orchestrator** — pure-LLM planner; picks `run_tool:<name>` / `chase` / `synthesize`.
- **tool_runner** — runs one mechanical tool → typed `Observation`, then evaluates it
  into findings (fused, because the frozen state has no transient observation channel).
- **hypothesis** — chases one surprise: candidate causes → sandboxed pandas probe →
  confirmed root cause written back to the ledger.
- **synthesizer** — deterministic report skeleton + ledger-built tables; LLM prose only.

## State & grounding

`EDAState` (`server/app/models/eda_schemas.py`) is the frozen contract. Accumulators
(`ledger`, `completed_passes`, `open_surprises`) use `operator.add` reducers; `budget`
keeps-latest. **No `llm`/`api_key` in state** — the per-request LLM is injected via
`config["configurable"]["llm"]`, keeping secrets out of the SQLite checkpoint.

Grounding chain: `tool output → Observation.id → Finding.evidence_ref → ledger →
traceable report claim`. `Finding.evidence_ref` is a required field — an ungrounded
finding cannot be constructed.

## Guards

| Guard | Where | What |
|-------|-------|------|
| Framing | framing.py | PII redaction + control-char strip + delimiter-neutralize on untrusted data; schema sanity-check + bounded re-prompt |
| Mechanical | tools/*.py | summaries-only, top-k truncation, output-size cap, seeded sampling |
| Sandbox | sandbox/executor.py | subprocess isolation, stripped pandas/numpy IO, restricted builtins, RLIMIT_CPU/AS |
| Orchestrator | orchestrator.py | hard probe-budget stop + repeat/coverage circuit-breaker (in code, not prompt) |
| Evaluator | evaluator.py | grounding keystone — every Finding carries `evidence_ref` |
| Synthesizer | synthesizer.py | claim-tracing (soft-flag unverified prose), propose-never-execute, pipe-safe tables |
| Hypothesis | hypothesis.py | probes run only in the sandbox; strict-boolean verdict; same untrusted-text hardening |

## Run lifecycle & streaming

`EDAService` (`server/app/agents/eda_agent.py`) drives the graph via `astream`,
translating node updates into typed `EDAEvent`s (phase_start / finding / interrupt /
report_ready / error) with monotonic ids into a **durable per-run buffer**. Routes
(`server/app/api/routes/eda.py`):

- `POST /eda/upload` — multipart, magic-byte validated (ingestion router).
- `POST /eda/run` — starts a background driving task, returns `{run_id}`.
- `GET /eda/stream/{run_id}` — SSE; replays the buffer honoring `Last-Event-ID` so a
  reconnect or post-resume second half is delivered without loss.
- `POST /eda/resume/{run_id}` — applies the human's edits at a checkpoint, resumes.
- `GET /eda/report/{ref}` — traversal-safe markdown download.

api_key lives only in an in-memory per-run store (dropped on terminal); the dataset is
leased for the run's lifetime so TTL cleanup can't delete it mid-run.

## Configuration

- **Prompts** — every node's prompt is a per-node YAML in `server/config/prompts/`
  loaded via `app/core/prompt_loader.py` (single-pass, injection-safe token
  substitution). Edit a prompt and restart the server to apply.
- **Tracing** — LangSmith is gated (`app/core/tracing.py`): a no-op without
  `LANGCHAIN_TRACING_V2`; when on, input/output masking defaults on so uploaded-data
  samples aren't shipped to traces. See `server/.env.example`.
- **Checkpointer** — SQLite (`server/.eda_runs/checkpoints.db`), durable/resumable.

## Key file map

```
server/app/
├── models/eda_schemas.py, eda_events.py     # frozen contracts
├── ingestion/                               # upload, storage (lease/TTL), loader
├── tools/                                   # first_contact, structural, univariate, bivariate, drift, registry
├── sandbox/                                 # hardened code executor
├── agents/eda/                              # framing, evaluator, synthesizer, orchestrator, hypothesis, graph
├── agents/eda_agent.py                      # EDAService (drive + SSE buffer)
├── api/routes/eda.py                        # run/stream/resume/report
└── core/                                    # prompt_loader, tracing, llm_factory, config
config/prompts/*.yaml                        # per-node prompt templates
client/src/components/workflows/eda/         # panel, upload, findings feed, interrupts, report
```

## Known limitations / follow-ups

- Budget defaults + evaluator severity thresholds are starting values; tune from a real
  live-LLM run.
- Secrets are in-memory only → a server restart makes paused runs non-resumable (the
  intended secret-hygiene trade-off).
- Sandbox is adequate for local single-user use, not a boundary against a determined
  attacker on a shared host.
