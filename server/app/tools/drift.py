"""Drift detection tool — Phase 5 of the EDA pass.

Compares the current DataFrame distribution against a reference DataFrame.
Computes per-column PSI (Population Stability Index) and KS statistic for
numeric columns.

Graceful no-op: if no reference_df is provided and none can be decoded from
state["provenance"], the tool returns status="no_reference" and sets no error.

Guard G_mech compliance:
- No raw row data emitted; only per-column statistics.
- Column results are top-k capped; excess reported as "n_more_cols".
- truncated=True is set when cap fires.
- Payload byte size is capped at _MAX_PAYLOAD_BYTES.
- seed=None (drift is exhaustive, no sampling needed for statistics).

Reference injection:
  Option A (preferred for orchestrator): pass reference_df as keyword arg.
  Option B (state-based): set state["provenance"] to a JSON string that contains
  key "reference_stats" (a dict of {col: {"values": [...]}}). This allows the
  orchestrator to serialise a reference profile into the graph state without
  adding a new EDAState field (which would be a Phase 1 contract change).
"""
from __future__ import annotations

import json
from uuid import uuid4

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from app.models.eda_schemas import EDAState, DriftObs

_TOP_K = 20
_MAX_PAYLOAD_BYTES = 524_288  # 512 KB
_PSI_BINS = 10
_EPS = 1e-8

# Severity thresholds (industry standard)
_PSI_WARN = 0.1
_PSI_CRITICAL = 0.2


# ── PSI ──────────────────────────────────────────────────────────────────────

def _psi(reference: np.ndarray, current: np.ndarray, bins: int = _PSI_BINS) -> float:
    """Population Stability Index between reference and current distributions."""
    # Use reference quantiles to define bin edges
    breakpoints = np.quantile(reference, np.linspace(0.0, 1.0, bins + 1))
    # Ensure unique edges (degenerate distributions have duplicate quantiles)
    breakpoints = np.unique(breakpoints)
    if len(breakpoints) < 2:
        return 0.0

    # Extend edges slightly so boundary values fall inside
    breakpoints[0] -= _EPS
    breakpoints[-1] += _EPS

    ref_counts, _ = np.histogram(reference, bins=breakpoints)
    cur_counts, _ = np.histogram(current, bins=breakpoints)

    ref_pct = ref_counts / max(ref_counts.sum(), 1)
    cur_pct = cur_counts / max(cur_counts.sum(), 1)

    # Avoid log(0): clip to EPS
    ref_pct = np.clip(ref_pct, _EPS, None)
    cur_pct = np.clip(cur_pct, _EPS, None)

    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


# ── Per-column drift stats ────────────────────────────────────────────────────

def _column_drift(
    ref_series: pd.Series, cur_series: pd.Series
) -> dict:
    ref_vals = ref_series.dropna().to_numpy(dtype=float)
    cur_vals = cur_series.dropna().to_numpy(dtype=float)

    if len(ref_vals) == 0 or len(cur_vals) == 0:
        return {"status": "insufficient_data"}

    psi_val = round(_psi(ref_vals, cur_vals), 6)
    ks_stat, ks_pval = ks_2samp(ref_vals, cur_vals)

    severity = "stable"
    if psi_val >= _PSI_CRITICAL:
        severity = "critical"
    elif psi_val >= _PSI_WARN:
        severity = "warn"

    return {
        "psi": psi_val,
        "ks_statistic": round(float(ks_stat), 6),
        "ks_pvalue": round(float(ks_pval), 6),
        "severity": severity,
        "ref_n": int(len(ref_vals)),
        "cur_n": int(len(cur_vals)),
        "ref_mean": round(float(ref_vals.mean()), 4),
        "cur_mean": round(float(cur_vals.mean()), 4),
    }


# ── Public entry point ────────────────────────────────────────────────────────

def run(
    df: pd.DataFrame,
    state: EDAState,
    *,
    reference_df: pd.DataFrame | None = None,
) -> DriftObs:
    """Return a DriftObs with per-column PSI/KS drift statistics.

    Args:
        df: Current batch DataFrame.
        state: EDA graph state.  If state["provenance"] is a JSON string with
            key "reference_stats", that profile is used as fallback reference.
        reference_df: Explicit reference DataFrame (takes priority over state).
    """
    truncated = False

    # ── Resolve reference ──────────────────────────────────────────────────
    if reference_df is None:
        # Attempt to decode a serialised reference profile from provenance
        provenance: str = state.get("provenance", "") or ""  # type: ignore[call-overload]
        if provenance.strip().startswith("{"):
            try:
                prov_data = json.loads(provenance)
                ref_stats = prov_data.get("reference_stats")
                if ref_stats:
                    # Reconstruct a DataFrame from the serialised column arrays
                    reference_df = pd.DataFrame(
                        {col: info["values"] for col, info in ref_stats.items()}
                    )
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    if reference_df is None:
        payload: dict = {
            "status": "no_reference",
            "message": (
                "No reference profile available.  Pass reference_df or encode "
                'reference_stats in state["provenance"] as JSON.'
            ),
            "columns_checked": [],
        }
        return DriftObs(id=str(uuid4()), seed=None, truncated=False, payload=payload)

    # ── Compute per-column drift for shared numeric columns ────────────────
    shared_cols = [
        col
        for col in df.columns
        if col in reference_df.columns
        and pd.api.types.is_numeric_dtype(df[col])
        and pd.api.types.is_numeric_dtype(reference_df[col])
    ]

    col_results: dict = {}
    for col in shared_cols[:_TOP_K]:
        col_results[col] = _column_drift(reference_df[col], df[col])

    n_more_cols = max(0, len(shared_cols) - _TOP_K)
    if n_more_cols > 0:
        truncated = True

    # Summary: columns flagged as warn or critical
    flagged = [
        col
        for col, stats in col_results.items()
        if stats.get("severity") in ("warn", "critical")
    ]

    payload = {
        "status": "computed",
        "columns_checked": list(col_results.keys()),
        "n_more_cols": n_more_cols,
        "per_column": col_results,
        "flagged_columns": flagged,
        "psi_thresholds": {"warn": _PSI_WARN, "critical": _PSI_CRITICAL},
    }

    # Hard output-size cap
    if len(json.dumps(payload, default=str).encode()) > _MAX_PAYLOAD_BYTES:
        # Trim per-column to top-5 by PSI descending
        top_cols = sorted(
            col_results.keys(),
            key=lambda c: col_results[c].get("psi", 0),
            reverse=True,
        )[:5]
        payload["per_column"] = {c: col_results[c] for c in top_cols}
        payload["truncated_note"] = "per_column trimmed to top-5 by PSI"
        truncated = True

    return DriftObs(
        id=str(uuid4()),
        seed=None,
        truncated=truncated,
        payload=payload,
    )
