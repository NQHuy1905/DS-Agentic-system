"""Bivariate profiling tool — Phase 4 of the EDA pass.

Checks: feature-feature Pearson correlations, collinearity flag for high-corr
pairs, feature-to-target relationships, grouped segment aggregations.

Guard G_mech compliance:
- Frames larger than _SAMPLE_THRESHOLD rows are sampled with seed=_SEED.
- Correlation pairs and segment rows are top-k capped; excess items reported
  as "n_more_*" counts.  truncated=True is set when any cap fires.
- Payload byte size is capped at _MAX_PAYLOAD_BYTES.
- No raw row data is ever emitted; only aggregates and pair statistics.
"""
from __future__ import annotations

import json
from uuid import uuid4

import numpy as np
import pandas as pd

from app.models.eda_schemas import EDAState, BivariateObs

_SEED = 42
_SAMPLE_THRESHOLD = 10_000
_TOP_K = 20
_MAX_PAYLOAD_BYTES = 524_288  # 512 KB
_HIGH_CORR_THRESHOLD = 0.8
_MAX_SEGMENT_CAT_COLS = 5    # limit segment loop to first N categorical cols
_MAX_SEGMENT_NUM_COLS = 5    # limit segment loop to first N numeric cols
_MAX_CARDINALITY_FOR_SEGMENT = 50  # skip high-cardinality cats as group keys


# ── Feature-feature correlations ─────────────────────────────────────────────

def _numeric_correlations(df: pd.DataFrame) -> dict:
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if len(num_cols) < 2:
        return {"top_pairs": [], "n_more_pairs": 0, "high_corr_pairs": []}

    corr_matrix = df[num_cols].corr()
    all_pairs: list[tuple[float, str, str]] = []

    for i, c1 in enumerate(num_cols):
        for c2 in num_cols[i + 1:]:
            val = corr_matrix.loc[c1, c2]
            if pd.isna(val):
                continue
            all_pairs.append((abs(float(val)), float(val), c1, c2))

    all_pairs.sort(reverse=True)  # highest |corr| first
    n_more = max(0, len(all_pairs) - _TOP_K)

    top_pairs = [
        {"col1": c1, "col2": c2, "pearson_r": round(r, 4)}
        for _, r, c1, c2 in all_pairs[:_TOP_K]
    ]
    high_corr_pairs = [
        {"col1": c1, "col2": c2, "pearson_r": round(r, 4)}
        for abs_r, r, c1, c2 in all_pairs
        if abs_r >= _HIGH_CORR_THRESHOLD
    ][:_TOP_K]

    return {
        "top_pairs": top_pairs,
        "n_more_pairs": n_more,
        "high_corr_pairs": high_corr_pairs,
        "high_corr_threshold": _HIGH_CORR_THRESHOLD,
    }


# ── Feature-to-target relationships ─────────────────────────────────────────

_TARGET_CANDIDATE_NAMES = frozenset({"target", "label", "y", "outcome", "response"})


def _target_relationships(df: pd.DataFrame, state: EDAState) -> dict:
    # Identify target column: check common names
    target_col: str | None = None
    for cname in _TARGET_CANDIDATE_NAMES:
        if cname in df.columns:
            target_col = cname
            break

    if target_col is None:
        return {"status": "no_target_column_identified"}

    num_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c != target_col]
    if not num_cols:
        return {"target_column": target_col, "status": "no_numeric_features"}

    relationships: list[dict] = []
    if pd.api.types.is_numeric_dtype(df[target_col]):
        for col in num_cols[:_TOP_K]:
            r = df[col].corr(df[target_col])
            if not pd.isna(r):
                relationships.append(
                    {"column": col, "pearson_r_with_target": round(float(r), 4)}
                )
        relationships.sort(key=lambda x: -abs(x["pearson_r_with_target"]))

    return {
        "target_column": target_col,
        "feature_target_correlations": relationships,
    }


# ── Grouped segment aggregations ─────────────────────────────────────────────

def _segment_aggregations(df: pd.DataFrame) -> list[dict]:
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if not cat_cols or not num_cols:
        return []

    aggs: list[dict] = []
    for cat_col in cat_cols[:_MAX_SEGMENT_CAT_COLS]:
        cardinality = df[cat_col].nunique()
        if cardinality < 2 or cardinality > _MAX_CARDINALITY_FOR_SEGMENT:
            continue
        for num_col in num_cols[:_MAX_SEGMENT_NUM_COLS]:
            grouped = df.groupby(cat_col, observed=True)[num_col].agg(
                ["mean", "std", "count"]
            )
            segments = [
                {
                    "segment": str(seg),
                    "mean": round(float(row["mean"]), 4),
                    "std": round(float(row["std"]) if not pd.isna(row["std"]) else 0.0, 4),
                    "count": int(row["count"]),
                }
                for seg, row in grouped.iterrows()
            ]
            if segments:
                aggs.append(
                    {
                        "groupby": cat_col,
                        "aggregated": num_col,
                        "segments": segments[:_TOP_K],
                        "n_more_segments": max(0, len(segments) - _TOP_K),
                    }
                )
    return aggs[:_TOP_K]


# ── Public entry point ───────────────────────────────────────────────────────

def run(df: pd.DataFrame, state: EDAState) -> BivariateObs:
    """Return a BivariateObs with correlations, target relationships, segments."""
    seed = _SEED
    truncated = False

    sampled = len(df) > _SAMPLE_THRESHOLD
    if sampled:
        df = df.sample(n=_SAMPLE_THRESHOLD, random_state=seed)

    corr_result = _numeric_correlations(df)
    if corr_result.get("n_more_pairs", 0) > 0:
        truncated = True

    payload: dict = {
        "sampled": sampled,
        "sample_seed": seed if sampled else None,
        "correlations": corr_result,
        "target_relationships": _target_relationships(df, state),
        "segment_aggregations": _segment_aggregations(df),
    }

    # Hard output-size cap: trim segment list first
    if len(json.dumps(payload, default=str).encode()) > _MAX_PAYLOAD_BYTES:
        payload["segment_aggregations"] = payload["segment_aggregations"][:5]
        truncated = True

    return BivariateObs(
        id=str(uuid4()),
        seed=seed if sampled else None,
        truncated=truncated,
        payload=payload,
    )
