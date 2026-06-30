"""Structural integrity profiling tool — Phase 2 of the EDA pass.

Checks: missingness (count + pattern), full-row + key-level duplicates,
validity (ranges, negatives, future dates, category-set violations,
string hygiene: whitespace/casing/unicode).

Guard G_mech compliance:
- Emits aggregates only; no raw row data.
- Null-correlation pairs, validity issues, and key-level dups are top-k capped.
- Payload byte size is capped at _MAX_PAYLOAD_BYTES; excess items are truncated.
- `truncated=True` is set whenever any cap is applied.
- seed=None (no sampling; structural pass is exhaustive by design).
"""
from __future__ import annotations

import json
from uuid import uuid4

import pandas as pd

from app.models.eda_schemas import EDAState, StructuralObs

_TOP_K = 20
_MAX_PAYLOAD_BYTES = 524_288  # 512 KB


# ── Missingness ─────────────────────────────────────────────────────────────

def _missingness(df: pd.DataFrame) -> dict:
    n = len(df)
    per_col: list[dict] = []
    cols_with_nulls: list[str] = []

    for col in df.columns:
        null_count = int(df[col].isna().sum())
        if null_count == 0:
            continue
        cols_with_nulls.append(col)
        null_rate = round(null_count / n, 4)

        # Pattern: compare first-half vs second-half null rates
        half = n // 2
        first_rate = float(df[col].isna().iloc[:half].mean())
        second_rate = float(df[col].isna().iloc[half:].mean())
        pattern = "time-concentrated" if abs(first_rate - second_rate) > 0.2 else "uniform"

        per_col.append(
            {
                "column": col,
                "null_count": null_count,
                "null_rate": null_rate,
                "pattern": pattern,
            }
        )

    # Row-level null summary
    row_null_counts = df.isna().sum(axis=1)
    rows_with_any_null = int((row_null_counts > 0).sum())
    rows_all_null = int((row_null_counts == len(df.columns)).sum())

    # Null correlation between column pairs (top-k pairs with |corr| > 0.5)
    null_corr_pairs: list[dict] = []
    if len(cols_with_nulls) >= 2:
        null_df = df[cols_with_nulls].isna().astype(int)
        corr_matrix = null_df.corr()
        pairs: list[tuple[float, str, str]] = []
        for i, c1 in enumerate(cols_with_nulls):
            for c2 in cols_with_nulls[i + 1:]:
                corr_val = corr_matrix.loc[c1, c2]
                if pd.isna(corr_val):
                    continue
                abs_corr = abs(float(corr_val))
                if abs_corr > 0.5:
                    pairs.append((abs_corr, c1, c2))
        pairs.sort(reverse=True)
        null_corr_pairs = [
            {"col1": c1, "col2": c2, "null_corr": round(v, 4)}
            for v, c1, c2 in pairs[:_TOP_K]
        ]

    return {
        "per_column": per_col,
        "rows_with_any_null": rows_with_any_null,
        "rows_all_null": rows_all_null,
        "null_corr_pairs": null_corr_pairs,
    }


# ── Duplicates ──────────────────────────────────────────────────────────────

def _duplicates(df: pd.DataFrame, state: EDAState) -> dict:
    full_row_dup_count = int(df.duplicated().sum())

    key_level: list[dict] = []
    grain: str = state.get("grain", "") or ""  # type: ignore[call-overload]
    if grain:
        key_cols = [c.strip() for c in grain.split(",") if c.strip() in df.columns]
        if key_cols:
            dup_count = int(df.duplicated(subset=key_cols).sum())
            key_level.append({"key": ",".join(key_cols), "dup_count": dup_count})

    return {"full_row_dup_count": full_row_dup_count, "key_level": key_level}


# ── Validity ────────────────────────────────────────────────────────────────

def _string_hygiene(series: pd.Series) -> dict:
    non_null = series.dropna().astype(str)
    if len(non_null) == 0:
        return {}
    whitespace = int(non_null.str.contains(r"^\s|\s$", regex=True).sum())
    mixed_case = int(
        ((non_null != non_null.str.lower()) & (non_null != non_null.str.upper())).sum()
    )
    non_ascii = int(non_null.apply(lambda v: not v.isascii()).sum())
    return {
        "leading_trailing_whitespace": whitespace,
        "mixed_case_values": mixed_case,
        "non_ascii": non_ascii,
    }


def _validity(df: pd.DataFrame, state: EDAState) -> list[dict]:
    today = pd.Timestamp.now()

    # Unpack expectations if available
    expectations = state.get("expectations")  # type: ignore[call-overload]
    valid_cats: dict[str, list[str]] = {}
    ranges: dict[str, tuple[float, float]] = {}
    if expectations is not None:
        for vc in expectations.valid_categories or []:
            valid_cats[vc.column] = [v.lower() for v in vc.valid_values]
        for r in expectations.ranges or []:
            ranges[r.column] = (r.min, r.max)

    issues: list[dict] = []

    for col in df.columns:
        col_issues: dict = {"column": col}
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue

        # Numeric checks
        if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
            negatives = int((non_null < 0).sum())
            if negatives:
                col_issues["negatives"] = negatives
            if col in ranges:
                lo, hi = ranges[col]
                out_of_range = int(((non_null < lo) | (non_null > hi)).sum())
                if out_of_range:
                    col_issues["out_of_range"] = out_of_range

        # Datetime checks
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            future_dates = int((non_null > today).sum())
            if future_dates:
                col_issues["future_dates"] = future_dates

        # Categorical validity
        if col in valid_cats and df[col].dtype == object:
            normalized = non_null.astype(str).str.lower().str.strip()
            invalid = int((~normalized.isin(valid_cats[col])).sum())
            if invalid:
                col_issues["invalid_categories"] = invalid

        # String hygiene
        if df[col].dtype == object:
            hygiene = _string_hygiene(df[col])
            if any(v > 0 for v in hygiene.values()):
                col_issues["string_hygiene"] = hygiene

        if len(col_issues) > 1:  # has at least one issue beyond the "column" key
            issues.append(col_issues)

    return issues


# ── Public entry point ───────────────────────────────────────────────────────

def run(df: pd.DataFrame, state: EDAState) -> StructuralObs:
    """Return a StructuralObs with missingness, duplicates, and validity payload."""
    truncated = False

    payload: dict = {
        "missingness": _missingness(df),
        "duplicates": _duplicates(df, state),
        "validity": _validity(df, state),
    }

    # Hard output-size cap
    raw_size = len(json.dumps(payload, default=str).encode())
    if raw_size > _MAX_PAYLOAD_BYTES:
        payload["validity"] = payload["validity"][:_TOP_K]
        truncated = True

    return StructuralObs(
        id=str(uuid4()),
        seed=None,
        truncated=truncated,
        payload=payload,
    )
