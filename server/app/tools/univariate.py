"""Univariate profiling tool — Phase 3 of the EDA pass.

Checks per column type:
- Numeric: distribution shape, center, spread, skew, kurtosis, IQR-based outliers.
- Categorical: cardinality, top-k frequency, rare categories, near-duplicate
  variants (e.g. "USA" / "U.S.A." normalise to the same token).
- Datetime: range, inferred granularity, gap count.

Guard G_mech compliance:
- Frames larger than _SAMPLE_THRESHOLD rows are sampled with seed=_SEED.
- Categorical frequency, rare-cat, and outlier lists are top-k capped; excess
  items are reported as "n_more_*" counts.  truncated=True is set when any cap
  fires.
- Payload byte size is capped at _MAX_PAYLOAD_BYTES.
"""
from __future__ import annotations

import json
import re
import unicodedata
from uuid import uuid4

import numpy as np
import pandas as pd

from app.models.eda_schemas import EDAState, UnivariateObs

_SEED = 42
_SAMPLE_THRESHOLD = 10_000
_TOP_K = 20
_MAX_PAYLOAD_BYTES = 524_288  # 512 KB


# ── String normalisation for near-dup detection ──────────────────────────────

def _normalise(s: str) -> str:
    """Fold unicode, lowercase, strip non-alphanumeric — "U.S.A." → "usa"."""
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ── Per-column analysers ─────────────────────────────────────────────────────

def _numeric_stats(series: pd.Series) -> dict:
    non_null = series.dropna()
    if len(non_null) == 0:
        return {"count": 0, "null_count": int(series.isna().sum())}

    q1 = float(non_null.quantile(0.25))
    q3 = float(non_null.quantile(0.75))
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    outlier_mask = (non_null < lower_fence) | (non_null > upper_fence)
    outlier_count = int(outlier_mask.sum())
    all_outlier_vals = non_null[outlier_mask].abs().sort_values(ascending=False)
    top_outliers = [round(float(v), 6) for v in non_null[outlier_mask].iloc[:_TOP_K]]
    n_more_outliers = max(0, outlier_count - _TOP_K)

    return {
        "count": int(len(non_null)),
        "null_count": int(series.isna().sum()),
        "mean": round(float(non_null.mean()), 6),
        "std": round(float(non_null.std()), 6),
        "min": float(non_null.min()),
        "p25": round(q1, 6),
        "median": round(float(non_null.median()), 6),
        "p75": round(q3, 6),
        "max": float(non_null.max()),
        "skew": round(float(non_null.skew()), 6),
        "kurtosis": round(float(non_null.kurt()), 6),
        "iqr": round(iqr, 6),
        "lower_fence": round(lower_fence, 6),
        "upper_fence": round(upper_fence, 6),
        "outlier_count": outlier_count,
        "top_outliers": top_outliers,
        "n_more_outliers": n_more_outliers,
    }


def _categorical_stats(series: pd.Series) -> dict:
    non_null = series.dropna()
    if len(non_null) == 0:
        return {"cardinality": 0, "null_count": int(series.isna().sum())}

    cardinality = int(non_null.nunique())
    total = len(non_null)
    freq = non_null.value_counts()

    # Top-k frequencies
    top_freq = [
        {"value": str(k), "count": int(v), "rate": round(v / total, 4)}
        for k, v in freq.head(_TOP_K).items()
    ]
    n_more_cats = max(0, cardinality - _TOP_K)

    # Rare categories: count ≤ 1 % of non-null total
    rare_threshold = max(1, int(total * 0.01))
    rare_series = freq[freq <= rare_threshold]
    n_rare = len(rare_series)
    rare_cats_sample = [str(k) for k in rare_series.head(_TOP_K).index]
    n_more_rare = max(0, n_rare - _TOP_K)

    # Near-duplicate variant detection via normalised key grouping
    groups: dict[str, list[str]] = {}
    for val in non_null.unique():
        norm_key = _normalise(str(val))
        groups.setdefault(norm_key, []).append(str(val))

    near_dup_variants = sorted(
        [
            {"normalised": nk, "variants": sorted(orig_list), "variant_count": len(orig_list)}
            for nk, orig_list in groups.items()
            if len(orig_list) > 1
        ],
        key=lambda x: -x["variant_count"],
    )
    n_more_nd = max(0, len(near_dup_variants) - _TOP_K)
    near_dup_variants = near_dup_variants[:_TOP_K]

    result: dict = {
        "cardinality": cardinality,
        "null_count": int(series.isna().sum()),
        "top_freq": top_freq,
        "n_more_cats": n_more_cats,
        "rare_cat_count": n_rare,
        "rare_cats_sample": rare_cats_sample,
        "n_more_rare": n_more_rare,
        "near_dup_variants": near_dup_variants,
        "n_more_near_dups": n_more_nd,
    }

    # Flag if strings look datetime-parseable
    str_sample = non_null.astype(str).head(50)
    try:
        # format="mixed" tells pandas 2.x to infer per-element without warning
        parsed_rate = float(
            pd.to_datetime(str_sample, errors="coerce", format="mixed").notna().mean()
        )
        if parsed_rate >= 0.8:
            result["parseable_as_datetime"] = True
            result["datetime_parse_rate"] = round(parsed_rate, 4)
    except Exception:
        pass

    return result


def _datetime_stats(series: pd.Series) -> dict:
    non_null = series.dropna()
    if len(non_null) == 0:
        return {"count": 0, "null_count": int(series.isna().sum())}

    sorted_vals = non_null.sort_values()
    diffs = sorted_vals.diff().dropna()

    if len(diffs) == 0:
        granularity = "unknown"
        gap_count = 0
    else:
        median_diff: pd.Timedelta = diffs.median()  # type: ignore[assignment]
        if median_diff < pd.Timedelta(hours=2):
            granularity = "hourly"
        elif median_diff < pd.Timedelta(days=2):
            granularity = "daily"
        elif median_diff < pd.Timedelta(weeks=2):
            granularity = "weekly"
        else:
            granularity = "monthly_or_coarser"
        gap_count = int((diffs > median_diff * 2).sum())

    return {
        "count": int(len(non_null)),
        "null_count": int(series.isna().sum()),
        "min": str(non_null.min()),
        "max": str(non_null.max()),
        "granularity": granularity,
        "gap_count": gap_count,
    }


# ── Public entry point ───────────────────────────────────────────────────────

def run(df: pd.DataFrame, state: EDAState) -> UnivariateObs:
    """Return a UnivariateObs with per-column numeric/categorical/datetime stats."""
    seed = _SEED
    truncated = False

    # Seeded sampling for large frames (G_mech)
    sampled = len(df) > _SAMPLE_THRESHOLD
    if sampled:
        df = df.sample(n=_SAMPLE_THRESHOLD, random_state=seed)

    numeric_stats: dict = {}
    categorical_stats: dict = {}
    datetime_stats: dict = {}

    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            datetime_stats[col] = _datetime_stats(df[col])
        elif (
            pd.api.types.is_numeric_dtype(df[col])
            and not pd.api.types.is_bool_dtype(df[col])
        ):
            numeric_stats[col] = _numeric_stats(df[col])
        else:
            categorical_stats[col] = _categorical_stats(df[col])

    # Check if any categorical column triggered top-k truncation
    for stats in categorical_stats.values():
        if stats.get("n_more_cats", 0) > 0:
            truncated = True
            break

    payload: dict = {
        "sampled": sampled,
        "sample_n": _SAMPLE_THRESHOLD if sampled else len(df),
        "sample_seed": seed,
        "numeric": numeric_stats,
        "categorical": categorical_stats,
        "datetime": datetime_stats,
    }

    # Hard output-size cap: trim top_freq lists first
    if len(json.dumps(payload, default=str).encode()) > _MAX_PAYLOAD_BYTES:
        for col_stats in categorical_stats.values():
            col_stats["top_freq"] = col_stats["top_freq"][:10]
        truncated = True

    return UnivariateObs(
        id=str(uuid4()),
        seed=seed,
        truncated=truncated,
        payload=payload,
    )
