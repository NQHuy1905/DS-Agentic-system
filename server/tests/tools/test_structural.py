"""Tests for app.tools.structural — Phase 3 mechanical profiling.

Fixture: structural_fixture.csv (20 rows) with planted ground truth:
  - full_row_dup_count = 1  (row index 10 is exact copy of row index 0)
  - user_id key-level dup_count = 3  (user_id 1, 5, 10 each appear twice)
  - age null_count = 2
  - age negatives = 3
  - created_at future dates = 2  (2027-01-13, 2028-06-14)
  - grade invalid (not in {A,B,C}) = 2
  - status leading/trailing whitespace = 2
  - status mixed-case values = 1
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.models.eda_schemas import (
    ColumnCategories,
    ExpectationModel,
    StructuralObs,
)
from app.tools import structural
from app.tools.registry import run_tool

FIXTURES = Path(__file__).parent / "fixtures"

# Planted ground truth (matches generate_fixtures.py STRUCTURAL_GROUND_TRUTH)
GT_FULL_ROW_DUPS = 1
GT_KEY_DUPS = 3
GT_AGE_NULLS = 2
GT_AGE_NEGATIVES = 3
GT_FUTURE_DATES = 2
GT_INVALID_GRADE = 2
GT_STATUS_WHITESPACE = 2
GT_STATUS_MIXED_CASE = 1


def _make_state(**overrides) -> dict:
    base: dict = {
        "dataset_ref": "test",
        "run_id": "r1",
        "objective": "testing",
        "grain": "user_id",
        "provenance": "",
        "expectations": ExpectationModel(
            valid_categories=[ColumnCategories(column="grade", valid_values=["A", "B", "C"])]
        ),
        "ledger": [],
        "completed_passes": [],
        "open_surprises": [],
        "budget": None,
        "next_action": "",
        "report": None,
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def struct_df() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "structural_fixture.csv", parse_dates=["created_at"])
    return df


@pytest.fixture(scope="module")
def obs(struct_df) -> StructuralObs:
    return structural.run(struct_df, _make_state())


# ── Contract ─────────────────────────────────────────────────────────────────

def test_returns_structural_obs(obs):
    assert isinstance(obs, StructuralObs)
    assert obs.tool == "structural"
    assert obs.id


def test_seed_is_none_no_sampling(obs):
    # structural is exhaustive — no seeded sampling
    assert obs.seed is None


# ── Duplicates ────────────────────────────────────────────────────────────────

def test_full_row_dup_count(obs):
    dups = obs.payload["duplicates"]
    assert dups["full_row_dup_count"] == GT_FULL_ROW_DUPS


def test_key_level_dup_count(obs):
    key_level = obs.payload["duplicates"]["key_level"]
    assert len(key_level) == 1
    assert key_level[0]["key"] == "user_id"
    assert key_level[0]["dup_count"] == GT_KEY_DUPS


def test_no_key_dup_without_grain(struct_df):
    obs_no_grain = structural.run(struct_df, _make_state(grain=""))
    assert obs_no_grain.payload["duplicates"]["key_level"] == []


# ── Missingness ───────────────────────────────────────────────────────────────

def test_age_null_count(obs):
    per_col = obs.payload["missingness"]["per_column"]
    age_entry = next((e for e in per_col if e["column"] == "age"), None)
    assert age_entry is not None, "age column should appear in missingness"
    assert age_entry["null_count"] == GT_AGE_NULLS


def test_null_rate_precision(obs):
    per_col = obs.payload["missingness"]["per_column"]
    age_entry = next(e for e in per_col if e["column"] == "age")
    expected_rate = round(GT_AGE_NULLS / 20, 4)
    assert age_entry["null_rate"] == pytest.approx(expected_rate, abs=1e-4)


def test_rows_with_any_null(obs):
    # Rows 14, 15 (0-indexed) have null age → 2 rows with any null
    assert obs.payload["missingness"]["rows_with_any_null"] == GT_AGE_NULLS


# ── Validity ─────────────────────────────────────────────────────────────────

def _get_validity(obs, column: str) -> dict | None:
    return next(
        (v for v in obs.payload["validity"] if v["column"] == column), None
    )


def test_age_negatives(obs):
    age_v = _get_validity(obs, "age")
    assert age_v is not None
    assert age_v["negatives"] == GT_AGE_NEGATIVES


def test_future_dates(obs):
    dt_v = _get_validity(obs, "created_at")
    assert dt_v is not None
    assert dt_v["future_dates"] == GT_FUTURE_DATES


def test_invalid_grade_categories(obs):
    grade_v = _get_validity(obs, "grade")
    assert grade_v is not None
    assert grade_v["invalid_categories"] == GT_INVALID_GRADE


def test_status_whitespace(obs):
    status_v = _get_validity(obs, "status")
    assert status_v is not None
    hygiene = status_v["string_hygiene"]
    assert hygiene["leading_trailing_whitespace"] == GT_STATUS_WHITESPACE


def test_status_mixed_case(obs):
    status_v = _get_validity(obs, "status")
    assert status_v is not None
    hygiene = status_v["string_hygiene"]
    assert hygiene["mixed_case_values"] == GT_STATUS_MIXED_CASE


def test_no_validity_issues_without_expectations(struct_df):
    obs_bare = structural.run(struct_df, _make_state(expectations=None))
    grade_v = _get_validity(obs_bare, "grade")
    # Without expectations, invalid_categories should not be populated
    if grade_v is not None:
        assert "invalid_categories" not in grade_v


# ── Determinism ───────────────────────────────────────────────────────────────

def test_deterministic(struct_df):
    state = _make_state()
    obs1 = structural.run(struct_df, state)
    obs2 = structural.run(struct_df, state)
    # Payloads identical; ids differ (fresh uuid each call)
    assert obs1.payload == obs2.payload


# ── Registry ─────────────────────────────────────────────────────────────────

def test_run_tool_dispatches_structural(struct_df):
    obs = run_tool("structural", struct_df, _make_state())
    assert isinstance(obs, StructuralObs)
