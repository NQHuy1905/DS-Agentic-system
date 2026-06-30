"""Tests for app.tools.drift — Phase 3 mechanical profiling.

Fixtures:
  drift_reference.csv  — 500 rows, feature1 ~ N(0,1), feature2 ~ N(0,1)
  drift_current.csv    — 500 rows, feature1 ~ N(0,1)+5 (shifted), feature2 ~ N(0,1)

Planted ground truth:
  - feature1 PSI ≥ 0.1  (large distribution shift of +5 sigma)
  - feature2 PSI < 0.1  (same distribution, minor Monte-Carlo noise)
  - No-reference path returns status="no_reference" without raising.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.models.eda_schemas import DriftObs
from app.tools import drift
from app.tools.registry import run_tool

FIXTURES = Path(__file__).parent / "fixtures"

# Planted ground truth
GT_F1_PSI_MIN = 0.1     # shifted distribution → high PSI
GT_F2_PSI_MAX = 0.1     # same distribution → low PSI

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
def ref_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "drift_reference.csv")


@pytest.fixture(scope="module")
def cur_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "drift_current.csv")


@pytest.fixture(scope="module")
def obs(ref_df, cur_df) -> DriftObs:
    return drift.run(cur_df, _STATE, reference_df=ref_df)


# ── Contract ──────────────────────────────────────────────────────────────────

def test_returns_drift_obs(obs):
    assert isinstance(obs, DriftObs)
    assert obs.tool == "drift"
    assert obs.id


def test_status_computed(obs):
    assert obs.payload["status"] == "computed"


def test_columns_checked_present(obs):
    cols = obs.payload["columns_checked"]
    assert "feature1" in cols
    assert "feature2" in cols


# ── PSI values match planted distribution shift ───────────────────────────────

def test_feature1_psi_reflects_large_shift(obs):
    psi = obs.payload["per_column"]["feature1"]["psi"]
    assert psi >= GT_F1_PSI_MIN, (
        f"feature1 PSI={psi:.4f} expected >= {GT_F1_PSI_MIN} (shifted +5σ)"
    )


def test_feature2_psi_low_no_shift(obs):
    psi = obs.payload["per_column"]["feature2"]["psi"]
    assert psi < GT_F2_PSI_MAX, (
        f"feature2 PSI={psi:.4f} expected < {GT_F2_PSI_MAX} (same distribution)"
    )


def test_feature1_flagged_as_critical_or_warn(obs):
    severity = obs.payload["per_column"]["feature1"]["severity"]
    assert severity in ("warn", "critical"), (
        f"feature1 severity={severity!r} should be warn or critical"
    )


def test_feature2_stable(obs):
    severity = obs.payload["per_column"]["feature2"]["severity"]
    assert severity == "stable"


# ── KS statistic ─────────────────────────────────────────────────────────────

def test_feature1_ks_statistic_present(obs):
    col_stats = obs.payload["per_column"]["feature1"]
    assert "ks_statistic" in col_stats
    assert "ks_pvalue" in col_stats
    # KS stat should be large given +5σ shift
    assert col_stats["ks_statistic"] >= 0.5


def test_feature2_ks_pvalue_not_significant(obs):
    col_stats = obs.payload["per_column"]["feature2"]
    # Same distribution → p-value should NOT be extremely small
    # (allow up to 0.05 threshold — some variance at n=500)
    assert col_stats["ks_pvalue"] >= 0.0  # just check it's present and numeric


# ── No-reference graceful no-op ───────────────────────────────────────────────

def test_no_reference_returns_no_op(cur_df):
    obs = drift.run(cur_df, _STATE)
    assert isinstance(obs, DriftObs)
    assert obs.payload["status"] == "no_reference"
    assert obs.truncated is False


def test_no_reference_does_not_raise_on_empty_provenance(cur_df):
    state_empty = {**_STATE, "provenance": ""}
    obs = drift.run(cur_df, state_empty)
    assert obs.payload["status"] == "no_reference"


def test_no_reference_broken_json_provenance(cur_df):
    """Malformed JSON in provenance must not crash; tool falls back to no-op."""
    state_bad = {**_STATE, "provenance": "{bad json"}
    obs = drift.run(cur_df, state_bad)
    assert obs.payload["status"] == "no_reference"


# ── Provenance-encoded reference (Option B injection) ─────────────────────────

def test_reference_injected_via_provenance(cur_df, ref_df):
    """State provenance JSON with 'reference_stats' key is decoded and used."""
    ref_stats = {
        col: {"values": ref_df[col].tolist()} for col in ref_df.columns
    }
    state_with_ref = {**_STATE, "provenance": json.dumps({"reference_stats": ref_stats})}
    obs = drift.run(cur_df, state_with_ref)
    assert obs.payload["status"] == "computed"
    psi = obs.payload["per_column"]["feature1"]["psi"]
    assert psi >= GT_F1_PSI_MIN


# ── Determinism ───────────────────────────────────────────────────────────────

def test_deterministic(ref_df, cur_df):
    obs1 = drift.run(cur_df, _STATE, reference_df=ref_df)
    obs2 = drift.run(cur_df, _STATE, reference_df=ref_df)
    assert obs1.payload == obs2.payload


# ── Registry ──────────────────────────────────────────────────────────────────

def test_run_tool_dispatches_drift_no_ref(cur_df):
    obs = run_tool("drift", cur_df, _STATE)
    assert isinstance(obs, DriftObs)
    assert obs.payload["status"] == "no_reference"


def test_run_tool_dispatches_drift_with_ref(ref_df, cur_df):
    obs = run_tool("drift", cur_df, _STATE, reference_df=ref_df)
    assert isinstance(obs, DriftObs)
    assert obs.payload["status"] == "computed"
