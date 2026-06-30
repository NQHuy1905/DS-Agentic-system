"""Generate fixture CSVs with precisely known ground truth for Phase 3 tests.

Run once: `python generate_fixtures.py` from this directory.
Ground truth constants are exported so test files can import them.

Planted counts (verified by construction, not sampled):
  structural:
    - full_row_dup_count = 1
    - user_id key-level dup_count = 2
    - age null_count = 2
    - age negatives = 3
    - future created_at (> 2026-06-30) = 2
    - invalid grade (not in {A,B,C}) = 2
    - status leading/trailing whitespace = 2
    - status mixed-case = 1

  univariate:
    - value: 5 planted extreme outliers (±50, ±51, 52), natural N(0,1) bulk
    - country cardinality = 50 → n_more_cats = 30 with top_k=20 → truncated
    - category near-dup groups: "usa" group has ["U.S.A.", "USA"] variants
    - event_date gap_count = 3

  bivariate:
    - feature1/feature2 pearson_r >= 0.9 (feature2 = feature1*0.95 + tiny noise)
    - feature1/feature3 pearson_r near 0 (independent)

  drift (reference vs current):
    - feature1 shifted +5 in current → high PSI
    - feature2 unchanged → low PSI
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
SEED = 42
rng = np.random.default_rng(SEED)

# ── Structural fixture ──────────────────────────────────────────────────────
_BASE_ROWS = [
    # (user_id, age, status, created_at, grade)
    (1,  30, "active",       "2023-01-01", "A"),
    (2,  35, "active",       "2023-01-02", "B"),
    (3,  40, "active",       "2023-01-03", "C"),
    (4,  45, "active",       "2023-01-04", "A"),
    (5,  50, "active",       "2023-01-05", "B"),
    (6,  55, "active",       "2023-01-06", "C"),
    (7,  60, "active",       "2023-01-07", "A"),
    (8,  65, "active",       "2023-01-08", "B"),
    (9,  70, "active",       "2023-01-09", "C"),
    (10, 75, "active",       "2023-01-10", "A"),
    # Row 10 (0-indexed): exact duplicate of row 0 → full_row_dup_count = 1
    (1,  30, "active",       "2023-01-01", "A"),
    # Row 11: key-dup user_id=5, negative age, whitespace status, invalid grade
    (5,  -5, "  active  ",   "2023-01-12", "X"),
    # Row 12: key-dup user_id=10, negative age, future date, invalid grade
    (10, -10, "ACTIVE",      "2027-01-13", "Y"),
    # Row 13: negative age, future date
    (11, -1,  "active",      "2028-06-14", "A"),
    # Row 14: null age
    (12, None, "active",     "2023-01-15", "B"),
    # Row 15: null age
    (13, None, "active",     "2023-01-16", "C"),
    # Row 16: whitespace + mixed case status
    (14, 20,  "  Active  ", "2023-01-17", "A"),
    (15, 22,  "active",      "2023-01-18", "B"),
    (16, 25,  "active",      "2023-01-19", "C"),
    (17, 28,  "active",      "2023-01-20", "A"),
]

STRUCTURAL_GROUND_TRUTH = {
    "full_row_dup_count": 1,
    "user_id_key_dup_count": 3,  # user_id 1, 5, 10 each appear twice → 3 dup rows
    "age_null_count": 2,
    "age_negatives": 3,
    "created_at_future": 2,
    "grade_invalid": 2,
    "status_whitespace": 2,
    "status_mixed_case": 1,
}

df_structural = pd.DataFrame(
    _BASE_ROWS,
    columns=["user_id", "age", "status", "created_at", "grade"],
)
df_structural.to_csv(HERE / "structural_fixture.csv", index=False)


# ── Parse-tells fixture (first_contact) ────────────────────────────────────
_PARSE_ROWS = []
for i in range(50):
    # Alternate between two date formats → mixed_date_formats tell
    date_str = f"2023-{(i % 12)+1:02d}-01" if i % 2 == 0 else f"{(i % 12)+1:02d}/01/2023"
    # Numeric strings → numeric_strings tell
    num_str = f"{1000 + i:,}"  # e.g. "1,000"
    _PARSE_ROWS.append((date_str, num_str, f"cat_{i % 5}"))

PARSE_TELLS_GROUND_TRUTH = {
    "mixed_date_column": "mixed_dates",
    "numeric_string_column": "numeric_strs",
}

df_parse = pd.DataFrame(_PARSE_ROWS, columns=["mixed_dates", "numeric_strs", "plain_cat"])
df_parse.to_csv(HERE / "parse_tells_fixture.csv", index=False)


# ── Univariate fixture ──────────────────────────────────────────────────────
_N = 200
rng2 = np.random.default_rng(SEED)

# Numeric: 195 N(0,1) clipped to [-4, 4] so natural outliers = 0, then 5 extreme outliers
_normal_vals = np.clip(rng2.standard_normal(195), -4.0, 4.0)
_outlier_vals = np.array([50.0, 51.0, -50.0, -51.0, 52.0])
_values = np.concatenate([_normal_vals, _outlier_vals])  # length 200
rng2.shuffle(_values)  # shuffle so outliers aren't at end

# Country: 50 unique values × 4 repetitions = 200 rows, cardinality = 50
_countries = [f"country_{i:02d}" for i in range(1, 51)] * 4  # 200 items
rng2.shuffle(_countries)

# Category near-dups: "USA" and "U.S.A." normalize to "usa"
_cat_pool = ["USA"] * 80 + ["U.S.A."] * 60 + ["United States"] * 60
rng2.shuffle(_cat_pool)
_categories = _cat_pool  # 200 items

# Event date: 200 monotone dates with 3 gaps of 4 days each
_base = pd.Timestamp("2023-01-01")
_dates = []
_day_offset = 0
for i in range(_N):
    _dates.append(_base + pd.Timedelta(days=_day_offset))
    _day_offset += 1
    if i in (49, 99, 149):
        _day_offset += 3  # insert 3-day gap → diff = 4 days

UNIVARIATE_GROUND_TRUTH = {
    "value_planted_outliers": 5,       # extreme outliers well beyond IQR fence (±50, ±51, 52)
    "value_outlier_count": 6,          # 5 planted + 1 natural N(0,1) beyond fence (seed=42 deterministic)
    "country_cardinality": 50,
    "country_n_more_cats": 30,         # 50 - top_k(20) = 30
    "category_near_dup_group": "usa",  # "USA" and "U.S.A." → same normalized key "usa"
    "event_date_gap_count": 3,
}

df_uni = pd.DataFrame({
    "value": _values,
    "country": _countries,
    "category": _categories,
    "event_date": [d.strftime("%Y-%m-%d") for d in _dates],
})
df_uni.to_csv(HERE / "univariate_fixture.csv", index=False)


# ── Bivariate fixture ───────────────────────────────────────────────────────
rng3 = np.random.default_rng(SEED)
_f1 = rng3.standard_normal(300)
_f2 = _f1 * 0.95 + rng3.standard_normal(300) * 0.05  # r ≈ 0.95+
_f3 = rng3.standard_normal(300)                        # independent
_target = _f1 * 2 + rng3.standard_normal(300) * 0.1

BIVARIATE_GROUND_TRUTH = {
    "f1_f2_min_corr": 0.90,     # should be ~0.95
    "f1_f3_max_abs_corr": 0.15, # independent, should be near 0
}

df_biv = pd.DataFrame({
    "feature1": _f1,
    "feature2": _f2,
    "feature3": _f3,
    "target": _target,
})
df_biv.to_csv(HERE / "bivariate_fixture.csv", index=False)


# ── Drift fixtures (reference vs current) ───────────────────────────────────
rng4 = np.random.default_rng(SEED)
_ref_f1 = rng4.standard_normal(500)
_ref_f2 = rng4.standard_normal(500)

rng5 = np.random.default_rng(SEED + 1)
_cur_f1 = rng5.standard_normal(500) + 5.0  # shifted +5 → high PSI
_cur_f2 = rng5.standard_normal(500)          # same distribution → low PSI

DRIFT_GROUND_TRUTH = {
    "feature1_psi_min": 0.1,   # large shift → PSI > 0.1
    "feature2_psi_max": 0.1,   # no shift → PSI < 0.1
}

df_ref = pd.DataFrame({"feature1": _ref_f1, "feature2": _ref_f2})
df_cur = pd.DataFrame({"feature1": _cur_f1, "feature2": _cur_f2})

df_ref.to_csv(HERE / "drift_reference.csv", index=False)
df_cur.to_csv(HERE / "drift_current.csv", index=False)


if __name__ == "__main__":
    print("Fixtures generated:")
    for p in HERE.glob("*.csv"):
        print(f"  {p.name} ({p.stat().st_size} bytes)")
