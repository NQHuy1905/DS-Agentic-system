"""Tests for app.tools.bivariate — Phase 3 mechanical profiling.

Fixture: bivariate_fixture.csv (300 rows) with planted ground truth:
  - feature2 = feature1 * 0.95 + tiny noise  → pearson_r ≥ 0.90
  - feature3 is independent of feature1       → |pearson_r| ≤ 0.15
  - target = feature1 * 2 + tiny noise        → strong correlation with feature1
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.models.eda_schemas import BivariateObs
from app.tools import bivariate
from app.tools.registry import run_tool

FIXTURES = Path(__file__).parent / "fixtures"

# Planted ground truth
GT_F1_F2_MIN_CORR = 0.90
GT_F1_F3_MAX_ABS_CORR = 0.15

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
def biv_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "bivariate_fixture.csv")


@pytest.fixture(scope="module")
def obs(biv_df) -> BivariateObs:
    return bivariate.run(biv_df, _STATE)


# ── Contract ──────────────────────────────────────────────────────────────────

def test_returns_bivariate_obs(obs):
    assert isinstance(obs, BivariateObs)
    assert obs.tool == "bivariate"
    assert obs.id


def test_correlations_section_present(obs):
    assert "correlations" in obs.payload
    corr = obs.payload["correlations"]
    assert "top_pairs" in corr
    assert "high_corr_pairs" in corr


# ── Feature-feature correlations ─────────────────────────────────────────────

def _find_pair(pairs: list[dict], a: str, b: str) -> dict | None:
    for p in pairs:
        if {p["col1"], p["col2"]} == {a, b}:
            return p
    return None


def test_f1_f2_high_correlation(obs):
    top_pairs = obs.payload["correlations"]["top_pairs"]
    pair = _find_pair(top_pairs, "feature1", "feature2")
    assert pair is not None, "feature1/feature2 should appear in top_pairs"
    assert pair["pearson_r"] >= GT_F1_F2_MIN_CORR


def test_f1_f2_flagged_in_high_corr_pairs(obs):
    high_corr = obs.payload["correlations"]["high_corr_pairs"]
    pair = _find_pair(high_corr, "feature1", "feature2")
    assert pair is not None, "feature1/feature2 should be in high_corr_pairs (r >= 0.8)"


def test_f1_f3_low_correlation(obs):
    top_pairs = obs.payload["correlations"]["top_pairs"]
    pair = _find_pair(top_pairs, "feature1", "feature3")
    if pair is not None:
        assert abs(pair["pearson_r"]) <= GT_F1_F3_MAX_ABS_CORR


def test_top_pairs_bounded(obs):
    assert len(obs.payload["correlations"]["top_pairs"]) <= 20


# ── Target relationships ──────────────────────────────────────────────────────

def test_target_column_identified(obs):
    tr = obs.payload["target_relationships"]
    assert tr.get("target_column") == "target"


def test_feature1_top_corr_with_target(obs):
    tr = obs.payload["target_relationships"]
    correlations = tr.get("feature_target_correlations", [])
    assert correlations, "Expected feature-target correlations"
    # feature1 should rank highest (it's the direct parent of target)
    top_col = correlations[0]["column"]
    assert top_col == "feature1", (
        f"Expected feature1 as top correlated, got {top_col}"
    )
    assert correlations[0]["pearson_r_with_target"] >= 0.90


# ── Determinism ───────────────────────────────────────────────────────────────

def test_deterministic(biv_df):
    obs1 = bivariate.run(biv_df, _STATE)
    obs2 = bivariate.run(biv_df, _STATE)
    assert obs1.payload == obs2.payload


# ── Sampling threshold (G_mech) ───────────────────────────────────────────────

def test_small_frame_not_sampled(obs):
    # 300 rows < _SAMPLE_THRESHOLD of 10_000
    assert obs.payload["sampled"] is False


def test_large_frame_sampled():
    import numpy as np

    rng = __import__("numpy").random.default_rng(1)
    big_df = pd.DataFrame(
        {
            "feature1": rng.standard_normal(15_000),
            "feature2": rng.standard_normal(15_000),
            "target": rng.standard_normal(15_000),
        }
    )
    obs = bivariate.run(big_df, _STATE)
    assert obs.payload["sampled"] is True


# ── Registry ──────────────────────────────────────────────────────────────────

def test_run_tool_dispatches_bivariate(biv_df):
    obs = run_tool("bivariate", biv_df, _STATE)
    assert isinstance(obs, BivariateObs)
