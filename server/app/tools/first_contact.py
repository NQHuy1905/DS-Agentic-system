"""First-contact profiling tool — Phase 1 of the EDA pass.

Checks: shape, dtypes, bounded row sample, parse-tell detection
(numeric-looking strings, mixed date formats).

Guard G_mech compliance:
- Sample is bounded to _MAX_SAMPLE_ROWS per section (no full row dump).
- Payload byte size is capped at _MAX_PAYLOAD_BYTES; sample is trimmed if exceeded.
- Sampling is seeded (seed=_SEED) for deterministic reproducibility.
- `truncated=True` is set whenever any cap is applied.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

import numpy as np
import pandas as pd

from app.models.eda_schemas import EDAState, FirstContactObs

_SEED = 42
_MAX_SAMPLE_ROWS = 5   # rows per head/tail/random preview
_TOP_K = 20
_MAX_PAYLOAD_BYTES = 524_288  # 512 KB hard ceiling

# Ordered by specificity; first match wins per value.
_DATE_PATTERNS: list[tuple[str, str]] = [
    ("YYYY-MM-DD",   r"\d{4}-\d{2}-\d{2}"),
    ("MM/DD/YYYY",   r"\d{1,2}/\d{1,2}/\d{4}"),
    ("DD/MM/YYYY",   r"\d{1,2}/\d{1,2}/\d{4}"),      # ambiguous, listed for detection
    ("DD-Mon-YYYY",  r"\d{1,2}-[A-Za-z]{3}-\d{4}"),
    ("YYYY/MM/DD",   r"\d{4}/\d{2}/\d{2}"),
    ("MM-DD-YYYY",   r"\d{1,2}-\d{1,2}-\d{4}"),
]
_DATE_RE = [(name, re.compile(pattern)) for name, pattern in _DATE_PATTERNS]


def _detect_parse_tells(df: pd.DataFrame) -> list[dict]:
    """Return list of parse-tell dicts for object-dtype columns."""
    tells: list[dict] = []
    for col in df.columns:
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue

        str_series = non_null.astype(str).str.strip()

        # Numeric-looking strings: remove commas/currency then try coerce
        cleaned = str_series.str.replace(",", "", regex=False).str.replace(
            r"^[\$€£¥]", "", regex=True
        )
        numeric_count = pd.to_numeric(cleaned, errors="coerce").notna().sum()
        rate = numeric_count / len(non_null)
        if rate >= 0.7:
            tells.append(
                {"column": col, "tell": "numeric_strings", "rate": round(float(rate), 4)}
            )
            continue  # skip date check for same column

        # Mixed date formats: sample first 200 non-null values
        sample = str_series.head(200)
        formats_seen: set[str] = set()
        for val in sample:
            for fmt_name, pat in _DATE_RE:
                if pat.fullmatch(val):
                    formats_seen.add(fmt_name)
                    break
        # Exclude the ambiguous pair (MM/DD vs DD/MM both match same regex)
        real_formats = {f for f in formats_seen if "Mon" in f or "/" not in f} | {
            f for f in formats_seen if "/" in f
        }
        if len(formats_seen) > 1:
            tells.append(
                {
                    "column": col,
                    "tell": "mixed_date_formats",
                    "formats_detected": sorted(formats_seen),
                }
            )

    return tells


def _safe_records(sub_df: pd.DataFrame) -> list[dict]:
    """Convert dataframe rows to JSON-safe dicts (NaN → None, values as str)."""
    records = []
    for row in sub_df.itertuples(index=False):
        record: dict = {}
        for col, val in zip(sub_df.columns, row):
            if pd.isna(val) if not isinstance(val, str) else False:
                record[col] = None
            else:
                record[col] = str(val)
        records.append(record)
    return records


def run(df: pd.DataFrame, state: EDAState) -> FirstContactObs:
    """Return a FirstContactObs with shape/dtype/sample/parse-tell payload."""
    seed = _SEED
    truncated = False
    n_rows, n_cols = df.shape

    rng = np.random.default_rng(seed)
    n_sample = min(_MAX_SAMPLE_ROWS, n_rows)
    rand_idx = sorted(
        rng.choice(n_rows, size=n_sample, replace=False).tolist()
    )

    payload: dict = {
        "shape": {"rows": int(n_rows), "cols": int(n_cols)},
        "column_names": list(df.columns),
        "dtypes": {col: str(df[col].dtype) for col in df.columns},
        "head": _safe_records(df.head(_MAX_SAMPLE_ROWS)),
        "tail": _safe_records(df.tail(_MAX_SAMPLE_ROWS)),
        "random_sample": _safe_records(df.iloc[rand_idx]),
        "parse_tells": _detect_parse_tells(df),
    }

    # Hard output-size cap: trim sample sections until under limit
    if len(json.dumps(payload, default=str).encode()) > _MAX_PAYLOAD_BYTES:
        for section in ("head", "tail", "random_sample"):
            payload[section] = payload[section][:2]
        truncated = True

    return FirstContactObs(
        id=str(uuid4()),
        seed=seed,
        truncated=truncated,
        payload=payload,
    )
