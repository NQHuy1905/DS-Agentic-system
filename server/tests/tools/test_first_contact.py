"""Tests for app.tools.first_contact — Phase 3 mechanical profiling.

Fixture: parse_tells_fixture.csv (50 rows)
  - mixed_dates column: alternates YYYY-MM-DD and MM/DD/YYYY → mixed_date_formats tell
  - numeric_strs column: formatted numbers "1,000" etc. → numeric_strings tell
  - plain_cat column: categorical strings → no tell

Ground truth is embedded here (matches generate_fixtures.py constants).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.models.eda_schemas import FirstContactObs
from app.tools import first_contact
from app.tools.registry import run_tool

FIXTURES = Path(__file__).parent / "fixtures"

# Minimal EDAState dict (TypedDict is a runtime dict)
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
def parse_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "parse_tells_fixture.csv")


@pytest.fixture(scope="module")
def structural_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "structural_fixture.csv")


# ── Contract tests ───────────────────────────────────────────────────────────

def test_returns_first_contact_obs(parse_df):
    obs = first_contact.run(parse_df, _STATE)
    assert isinstance(obs, FirstContactObs)
    assert obs.tool == "first_contact"
    assert obs.id  # non-empty UUID string
    assert obs.seed == 42


def test_shape_matches_dataframe(structural_df):
    obs = first_contact.run(structural_df, _STATE)
    shape = obs.payload["shape"]
    assert shape["rows"] == len(structural_df)
    assert shape["cols"] == len(structural_df.columns)


def test_column_names_present(structural_df):
    obs = first_contact.run(structural_df, _STATE)
    assert obs.payload["column_names"] == list(structural_df.columns)


def test_dtypes_present(structural_df):
    obs = first_contact.run(structural_df, _STATE)
    dtypes = obs.payload["dtypes"]
    assert set(dtypes.keys()) == set(structural_df.columns)


# ── Sample bounds (G_mech: no raw row dump) ──────────────────────────────────

def test_head_bounded(structural_df):
    obs = first_contact.run(structural_df, _STATE)
    assert len(obs.payload["head"]) <= 5


def test_tail_bounded(structural_df):
    obs = first_contact.run(structural_df, _STATE)
    assert len(obs.payload["tail"]) <= 5


def test_random_sample_bounded_and_deterministic(structural_df):
    obs1 = first_contact.run(structural_df, _STATE)
    obs2 = first_contact.run(structural_df, _STATE)
    sample1 = obs1.payload["random_sample"]
    sample2 = obs2.payload["random_sample"]
    assert len(sample1) <= 5
    # Deterministic: same seed → same indices
    assert sample1 == sample2


# ── Parse-tell detection ─────────────────────────────────────────────────────

def test_numeric_strings_tell_detected(parse_df):
    obs = first_contact.run(parse_df, _STATE)
    tells = obs.payload["parse_tells"]
    numeric_tell = next(
        (t for t in tells if t["tell"] == "numeric_strings" and t["column"] == "numeric_strs"),
        None,
    )
    assert numeric_tell is not None, f"Expected numeric_strings tell, got: {tells}"
    assert numeric_tell["rate"] >= 0.7


def test_mixed_date_formats_tell_detected(parse_df):
    obs = first_contact.run(parse_df, _STATE)
    tells = obs.payload["parse_tells"]
    date_tell = next(
        (t for t in tells if t["tell"] == "mixed_date_formats" and t["column"] == "mixed_dates"),
        None,
    )
    assert date_tell is not None, f"Expected mixed_date_formats tell, got: {tells}"
    assert len(date_tell["formats_detected"]) >= 2


def test_plain_cat_no_tell(parse_df):
    obs = first_contact.run(parse_df, _STATE)
    tells = obs.payload["parse_tells"]
    plain_tells = [t for t in tells if t["column"] == "plain_cat"]
    assert plain_tells == [], f"plain_cat should not generate a tell, got: {plain_tells}"


# ── Registry dispatch ────────────────────────────────────────────────────────

def test_run_tool_dispatches_first_contact(parse_df):
    obs = run_tool("first_contact", parse_df, _STATE)
    assert isinstance(obs, FirstContactObs)


def test_run_tool_unknown_name_raises(parse_df):
    with pytest.raises(KeyError, match="Unknown tool"):
        run_tool("nonexistent", parse_df, _STATE)
