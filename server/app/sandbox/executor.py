"""Sandbox executor: runs LLM-generated code in a restricted subprocess.

Public API
----------
    run_code(code, dataset_path, timeout_s=10, mem_mb=None) -> SandboxResult

Caller contract
---------------
The caller resolves dataset_ref → dataset_path before calling run_code.
This keeps a clean seam: the sandbox knows nothing about the ingestion layer.

Security posture (localhost-only assumption)
---------------------------------------------
This sandbox is NOT a security boundary against determined attackers on a
shared or internet-facing host. It is adequate for single-user local use.
For any non-local deployment wrap the server in a container / gVisor / nsjail.

Defense layers (outermost → innermost):
  1. AST policy check (parent) — rejects before spawning.
  2. Subprocess isolation (spawn context) — no shared memory with parent.
  3. Capability stripping (child) — removes I/O methods from pandas/numpy.
  4. Restricted __builtins__ (child) — no open/eval/exec/breakpoint.
  5. RLIMIT_CPU + RLIMIT_AS (child) — bound wall-time and address space.
  6. prctl(PR_SET_PDEATHSIG, SIGKILL) (child) — dies with uvicorn on reload.
  7. Network: URL-reader caps stripped; no seccomp/netns (requires root).
     Document: a determined attacker can still reach network via ctypes/cffi.
"""
from __future__ import annotations

import ast
import ctypes
import io
import multiprocessing
import os
import resource
import time
import traceback
from contextlib import redirect_stdout
from typing import Any, Optional

import pandas as pd

from app.sandbox.models import SandboxResult
from app.sandbox.policy import (
    BANNED_NUMPY_ATTRS,
    BANNED_PANDAS_ATTRS,
    PolicyViolation,
    check_policy,
    make_restricted_builtins,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# After loading df, RLIMIT_AS = current_vm + max(frame * MULTIPLIER, BASE_OVERHEAD).
# A 200 MB CSV expands to 1–2 GB resident; multiplier 5 leaves room for
# intermediate computations without permitting unbounded growth.
_FRAME_AS_MULTIPLIER = 5
_RUNTIME_OVERHEAD_BYTES = 512 * 1024 * 1024  # 512 MB baseline

# Linux prctl constants
_PR_SET_PDEATHSIG = 1
_SIGKILL = 9

# Disabled-capability sentinel used to neuter pandas/numpy I/O methods.
_DISABLED_CAPS = frozenset(BANNED_PANDAS_ATTRS | BANNED_NUMPY_ATTRS)


# ---------------------------------------------------------------------------
# Child-process helpers (all called inside the spawned process)
# ---------------------------------------------------------------------------

def _install_pdeathsig() -> None:
    """Kill this child when the parent (uvicorn) dies or hot-reloads."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, _SIGKILL, 0, 0, 0)
    except Exception:
        pass  # Non-Linux or prctl unavailable — best-effort


def _read_vm_size_bytes() -> int:
    """Return VmSize from /proc/self/status in bytes, or 0 on failure."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _capability_disabled(*args: Any, **kwargs: Any) -> None:  # type: ignore[return]
    raise PermissionError(
        "Sandbox policy: this pandas/numpy I/O capability is disabled."
    )


def _strip_capabilities() -> None:
    """Replace dangerous I/O methods on pandas/numpy with a sentinel.

    Module allowlist != capability allowlist:
    - pd.read_pickle("http://x.pkl") triggers pickle __reduce__ → RCE
    - pd.read_csv("/etc/passwd") → arbitrary file read
    - df.to_csv("out.csv") → arbitrary write
    All without touching the 'open' builtin or triggering import bans.
    """
    try:
        import pandas as _pd

        for attr in BANNED_PANDAS_ATTRS:
            if hasattr(_pd, attr):
                try:
                    setattr(_pd, attr, _capability_disabled)
                except (AttributeError, TypeError):
                    pass
        # DataFrame instance methods
        for attr in (
            "to_csv", "to_parquet", "to_json", "to_excel",
            "to_html", "to_pickle", "to_hdf", "to_feather",
            "to_orc", "to_stata", "to_gbq",
        ):
            if hasattr(_pd.DataFrame, attr):
                try:
                    setattr(_pd.DataFrame, attr, _capability_disabled)
                except (AttributeError, TypeError):
                    pass
    except Exception:
        pass

    try:
        import numpy as _np

        for attr in BANNED_NUMPY_ATTRS:
            if hasattr(_np, attr):
                try:
                    setattr(_np, attr, _capability_disabled)
                except (AttributeError, TypeError):
                    pass
    except Exception:
        pass


def _set_resource_limits(
    frame_bytes: int, timeout_s: int, mem_mb: Optional[int]
) -> None:
    """Apply RLIMIT_CPU and RLIMIT_AS inside the child.

    CPU limit: timeout_s + 2 s CPU time (wall-clock kill is parent's job).
    AS limit: sized relative to the loaded frame so a large CSV isn't
    immediately OOM-killed before any computation runs.
    """
    cpu_limit = timeout_s + 2
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    except Exception:
        pass

    if mem_mb is not None:
        as_limit = mem_mb * 1024 * 1024
    else:
        vm_current = _read_vm_size_bytes()
        headroom = max(frame_bytes * _FRAME_AS_MULTIPLIER, _RUNTIME_OVERHEAD_BYTES)
        as_limit = vm_current + headroom

    try:
        resource.setrlimit(resource.RLIMIT_AS, (as_limit, as_limit))
    except Exception:
        pass


def _transform_last_expr(code: str) -> str:
    """Rewrite the final expression statement to assign __sandbox_result__.

    Allows single-expression probes like ``df['x'].mean()`` to surface a
    return value without the caller needing an explicit assignment.
    Returns original code unchanged if the last node is not an Expr.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except Exception:
        return code

    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return code

    last = tree.body[-1]
    assign = ast.Assign(
        targets=[ast.Name(id="__sandbox_result__", ctx=ast.Store())],
        value=last.value,
        lineno=last.lineno,
        col_offset=last.col_offset,
    )
    ast.fix_missing_locations(assign)
    tree.body[-1] = assign

    try:
        return ast.unparse(tree)  # Python 3.9+
    except Exception:
        # Fallback: append explicit assignment after the original code
        return code + "\n__sandbox_result__ = None"


def _to_serialisable(value: Any) -> Any:
    """Convert value to something pydantic/JSON can round-trip, or repr."""
    import json
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _child_worker(
    code: str,
    dataset_path: str,
    timeout_s: int,
    mem_mb: Optional[int],
    result_queue: "multiprocessing.Queue[SandboxResult]",
) -> None:
    """Entry point for the spawned child process. Never raises."""
    _install_pdeathsig()
    t_start = time.perf_counter()
    stdout_buf = io.StringIO()

    def _put(result: SandboxResult) -> None:
        result_queue.put(result)

    try:
        # --- Load dataset BEFORE setting AS limit so the load itself is free ---
        ext = os.path.splitext(dataset_path)[1].lower()
        try:
            if ext == ".parquet":
                df = pd.read_parquet(dataset_path)
            else:
                df = pd.read_csv(dataset_path)
        except Exception as exc:
            _put(SandboxResult(
                ok=False,
                error=f"Dataset load failed: {exc}",
                duration=time.perf_counter() - t_start,
            ))
            return

        frame_bytes = int(df.memory_usage(deep=True).sum())

        # --- Strip I/O capabilities from pandas/numpy ---
        _strip_capabilities()

        # --- Apply resource limits relative to loaded frame ---
        _set_resource_limits(frame_bytes, timeout_s, mem_mb)

        # --- Policy check (defence-in-depth; parent already checked) ---
        try:
            check_policy(code)
        except PolicyViolation as exc:
            _put(SandboxResult(
                ok=False,
                error=f"PolicyViolation: {exc}",
                duration=time.perf_counter() - t_start,
            ))
            return

        # --- Build execution namespace ---
        import math as _math
        import numpy as _np
        import statistics as _stats

        safe_builtins = make_restricted_builtins()
        namespace: dict = {
            "__builtins__": safe_builtins,
            "df": df,
            "pd": pd,
            "np": _np,
            "math": _math,
            "statistics": _stats,
        }

        transformed = _transform_last_expr(code)

        # --- Execute ---
        try:
            with redirect_stdout(stdout_buf):
                exec(  # noqa: S102 — intentional sandbox exec
                    compile(transformed, "<sandbox>", "exec"),
                    namespace,
                )
            value = _to_serialisable(namespace.get("__sandbox_result__"))
            _put(SandboxResult(
                ok=True,
                value=value,
                stdout=stdout_buf.getvalue(),
                duration=time.perf_counter() - t_start,
            ))
        except Exception as exc:
            _put(SandboxResult(
                ok=False,
                stdout=stdout_buf.getvalue(),
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                duration=time.perf_counter() - t_start,
            ))

    except Exception as exc:
        _put(SandboxResult(
            ok=False,
            stdout=stdout_buf.getvalue() if "stdout_buf" in dir() else "",
            error=f"Child fatal: {type(exc).__name__}: {exc}",
            duration=time.perf_counter() - t_start,
        ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_code(
    code: str,
    dataset_path: str,
    timeout_s: int = 10,
    mem_mb: Optional[int] = None,
) -> SandboxResult:
    """Execute *code* in a sandboxed subprocess; never raises.

    Args:
        code:         Python source string. May reference pre-loaded ``df``
                      (pandas DataFrame), ``pd``, ``np``, ``math``, ``statistics``.
        dataset_path: Path to a ``.parquet`` or ``.csv`` file. The caller
                      resolves dataset_ref → dataset_path (seam with ingestion).
        timeout_s:    Wall-clock timeout in seconds. Default 10.
        mem_mb:       Optional explicit virtual-address-space cap in MB.
                      If None, sized as current_vm + max(frame*5, 512 MB).

    Returns:
        SandboxResult with ok/value/stdout/error/duration fields.
    """
    # Fast policy check in the parent — avoids spawning a process for bad code
    try:
        check_policy(code)
    except PolicyViolation as exc:
        return SandboxResult(ok=False, error=f"PolicyViolation: {exc}")

    ctx = multiprocessing.get_context("spawn")
    result_queue: "multiprocessing.Queue[SandboxResult]" = ctx.Queue()

    proc = ctx.Process(
        target=_child_worker,
        args=(code, dataset_path, timeout_s, mem_mb, result_queue),
        daemon=True,
    )
    t_start = time.perf_counter()
    proc.start()

    result: Optional[SandboxResult] = None
    try:
        result = result_queue.get(timeout=timeout_s)
    except Exception:
        # Queue.get timed out or was interrupted
        result = None
    finally:
        # Reap child — must happen on every exit path (timeout, exception, success)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.join(timeout=2)
        except Exception:
            pass

    if result is None:
        return SandboxResult(
            ok=False,
            error="Sandbox timeout: code exceeded wall-clock time limit.",
            duration=time.perf_counter() - t_start,
        )

    return result
