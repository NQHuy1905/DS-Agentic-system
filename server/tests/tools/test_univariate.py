"""Tests for app.tools.univariate — Phase 3 mechanical profiling.

Fixture: univariate_fixture.csv (200 rows) with planted ground truth:
  - value: 195 N(0,1) clipped to [-4,4] + 5 extreme outliers (±50, ±51, 52)
      → outlier_count = 6 (5 planted + 1 natural; seed=42 deterministic)
  - country: 50 unique values × 4 reps → cardinality=50 → n_more_cats=30,
      truncated=True with top_k=20
  - category: "USA" (80) + "U.S.A." (60) + "United States" (60)
      → near-dup group "usa" with variants ["U.S.A.", "USA"]
  - event_date: sequential dates with 3 inserted 4-day gaps → gap_count=3
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.models.eda_schemas import UnivariateObs
from app.tools import univariate
from app.tools.registry import run_tool

FIXTURES = Path(__file__).parent / "fixtures"

# Planted ground truth
GT_VALUE_OUTLIER_COUNT = 6   # deterministic at seed=42
GT_COUNTRY_CARDINALITY = 50
GT_COUNTRY_N_MORE = 30       # 50 - top_k(20)
GT_EVENT_DATE_GAPS = 3

_STATE: dict = {
    "dataset_ref": "test",
    "run_id": "r1",
    "objective": "testing",
    "grain": "",
    "provenance": "",
    "expectations": None,
    "ledger": [],
    "completed_passes": [],
    "open_surprises": [],
    "budget": None,
    "next_action": "",
    "report": None,
}


@pytest.fixture(scope="module")
def uni_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "univariate_fixture.csv", parse_dates=["event_date"])
    return df


@pytest.fixture(scope="module")
def obs(uni_df) -> UnivariateObs:
    return univariate.run(uni_df, _STATE)


# ── Contract ──────────────────────────────────────────────────────────────────

def test_returns_univariate_obs(obs):
    assert isinstance(obs, UnivariateObs)
    assert obs.tool == "univariate"
    assert obs.id
    assert obs.seed == 42


# ── Numeric: outlier detection (IQR method) ───────────────────────────────────

def test_value_outlier_count_matches_planted(obs):
    value_stats = obs.payload["numeric"]["value"]
    assert value_stats["outlier_count"] == GT_VALUE_OUTLIER_COUNT


def test_value_top_outliers_bounded(obs):
    value_stats = obs.payload["numeric"]["value"]
    assert len(value_stats["top_outliers"]) <= 20


def test_value_contains_extreme_planted_outliers(obs):
    value_stats = obs.payload["numeric"]["value"]
    top = value_stats["top_outliers"]
    # At least one value should be close to our planted ±50/±51/52 outliers
    assert any(abs(v) >= 49.0 for v in top), f"Expected extreme outlier in top list: {top}"


def test_value_stats_structure(obs):
    stats = obs.payload["numeric"]["value"]
    for key in ("count", "mean", "std", "min", "p25", "median", "p75", "max", "skew"):
        assert key in stats, f"Missing key {key!r} in numeric stats"


# ── Categorical: cardinality + top-k truncation (G_mech) ─────────────────────

def test_country_cardinality(obs):
    country_stats = obs.payload["categorical"]["country"]
    assert country_stats["cardinality"] == GT_COUNTRY_CARDINALITY


def test_country_truncated_true(obs):
    """High-cardinality column must set truncated=True on the observation."""
    assert obs.truncated is True


def test_country_n_more_cats(obs):
    country_stats = obs.payload["categorical"]["country"]
    assert country_stats["n_more_cats"] == GT_COUNTRY_N_MORE


def test_country_top_freq_bounded(obs):
    country_stats = obs.payload["categorical"]["country"]
    assert len(country_stats["top_freq"]) <= 20


# ── Categorical: near-duplicate variant detection ─────────────────────────────

def test_category_near_dup_group_usa(obs):
    cat_stats = obs.payload["categorical"]["category"]
    nd_variants = cat_stats["near_dup_variants"]
    usa_group = next(
        (g for g in nd_variants if g["normalised"] == "usa"),
        None,
    )
    assert usa_group is not None, (
        f"Expected a near-dup group 'usa' in {[g['normalised'] for g in nd_variants]}"
    )
    assert set(usa_group["variants"]) == {"USA", "U.S.A."}
    assert usa_group["variant_count"] == 2


def test_near_dup_variants_bounded(obs):
    cat_stats = obs.payload["categorical"]["category"]
    assert len(cat_stats["near_dup_variants"]) <= 20


# ── Datetime: gap detection ───────────────────────────────────────────────────

def test_event_date_gap_count(obs):
    dt_stats = obs.payload["datetime"]["event_date"]
    assert dt_stats["gap_count"] == GT_EVENT_DATE_GAPS


def test_event_date_granularity_daily(obs):
    dt_stats = obs.payload["datetime"]["event_date"]
    assert dt_stats["granularity"] == "daily"


def test_event_date_range_present(obs):
    dt_stats = obs.payload["datetime"]["event_date"]
    assert "min" in dt_stats and "max" in dt_stats


# ── Determinism ───────────────────────────────────────────────────────────────

def test_deterministic(uni_df):
    obs1 = univariate.run(uni_df, _STATE)
    obs2 = univariate.run(uni_df, _STATE)
    assert obs1.payload == obs2.payload
    assert obs1.truncated == obs2.truncated


# ── Sampling threshold (G_mech) ───────────────────────────────────────────────

def test_small_frame_not_sampled(uni_df):
    obs = univariate.run(uni_df, _STATE)
    # Fixture is 200 rows — below _SAMPLE_THRESHOLD of 10_000
    assert obs.payload["sampled"] is False


def test_large_frame_sampled():
    import numpy as np
    import pandas as pd

    rng = pd.DataFrame({"x": np.random.default_rng(1).standard_normal(15_000)})
    obs = univariate.run(rng, _STATE)
    assert obs.payload["sampled"] is True
    assert obs.payload["sample_n"] == 10_000


# ── Registry ──────────────────────────────────────────────────────────────────

def test_run_tool_dispatches_univariate(uni_df):
    obs = run_tool("univariate", uni_df, _STATE)
    assert isinstance(obs, UnivariateObs)
