"""Deterministic evaluator tests — no LLM, synthetic (expected, observed) pairs."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.eda.evaluator import evaluate, make_evaluation_partial
from app.models.eda_schemas import (
    BivariateObs,
    ColumnCategories,
    ColumnDtype,
    ColumnNullPrior,
    ColumnRange,
    DriftObs,
    ExpectationModel,
    Finding,
    FirstContactObs,
    StructuralObs,
    UnivariateObs,
)


# --------------------------------------------------------------------------- #
# Grounding keystone
# --------------------------------------------------------------------------- #
def test_finding_requires_evidence_ref():
    """A finding cannot be constructed without tracing to an observation."""
    with pytest.raises(ValidationError):
        Finding(id="x", phase="p", severity="info", description="d")  # type: ignore[call-arg]


def test_all_findings_carry_evidence_ref():
    obs = StructuralObs(
        id="obs-1",
        payload={"missingness": {"per_column": [{"column": "income", "null_rate": 0.40}]}},
    )
    exp = ExpectationModel(null_priors=[ColumnNullPrior(column="income", expected_null_rate=0.01)])
    findings, _ = evaluate([obs], exp)
    assert findings
    assert all(f.evidence_ref == "obs-1" for f in findings)


# --------------------------------------------------------------------------- #
# Null-rate rubric
# --------------------------------------------------------------------------- #
def test_null_rate_far_above_prior_is_critical():
    obs = StructuralObs(
        id="o", payload={"missingness": {"per_column": [{"column": "income", "null_rate": 0.40}]}}
    )
    exp = ExpectationModel(null_priors=[ColumnNullPrior(column="income", expected_null_rate=0.01)])
    findings, _ = evaluate([obs], exp)
    assert [f.severity for f in findings] == ["critical"]


def test_null_rate_mild_excess_is_warn():
    obs = StructuralObs(
        id="o", payload={"missingness": {"per_column": [{"column": "x", "null_rate": 0.06}]}}
    )
    exp = ExpectationModel(null_priors=[ColumnNullPrior(column="x", expected_null_rate=0.02)])
    findings, _ = evaluate([obs], exp)
    assert [f.severity for f in findings] == ["warn"]


def test_null_rate_within_prior_emits_nothing():
    obs = StructuralObs(
        id="o", payload={"missingness": {"per_column": [{"column": "x", "null_rate": 0.02}]}}
    )
    exp = ExpectationModel(null_priors=[ColumnNullPrior(column="x", expected_null_rate=0.02)])
    findings, surprises = evaluate([obs], exp)
    assert findings == [] and surprises == []


# --------------------------------------------------------------------------- #
# Dtype mismatch
# --------------------------------------------------------------------------- #
def test_dtype_object_vs_int_is_critical():
    obs = FirstContactObs(id="o", payload={"dtypes": {"age": "object"}})
    exp = ExpectationModel(expected_dtypes=[ColumnDtype(column="age", expected_dtype="int")])
    findings, _ = evaluate([obs], exp)
    assert [f.severity for f in findings] == ["critical"]


def test_dtype_int_vs_float_is_benign_info():
    obs = FirstContactObs(id="o", payload={"dtypes": {"age": "float64"}})
    exp = ExpectationModel(expected_dtypes=[ColumnDtype(column="age", expected_dtype="int")])
    findings, _ = evaluate([obs], exp)
    assert [f.severity for f in findings] == ["info"]


# --------------------------------------------------------------------------- #
# Range / validity breach
# --------------------------------------------------------------------------- #
def test_negative_age_range_breach_is_critical():
    obs = UnivariateObs(id="o", payload={"numeric": {"age": {"min": -3.0, "max": 90.0}}})
    exp = ExpectationModel(ranges=[ColumnRange(column="age", min=0.0, max=120.0)])
    findings, _ = evaluate([obs], exp)
    assert len(findings) == 1 and findings[0].severity == "critical"


def test_in_range_emits_nothing():
    obs = UnivariateObs(id="o", payload={"numeric": {"age": {"min": 1.0, "max": 90.0}}})
    exp = ExpectationModel(ranges=[ColumnRange(column="age", min=0.0, max=120.0)])
    findings, _ = evaluate([obs], exp)
    assert findings == []


def test_invalid_category_is_warn():
    obs = UnivariateObs(
        id="o",
        payload={"categorical": {"status": {"top_freq": [{"value": "active"}, {"value": "ZZZ"}]}}},
    )
    exp = ExpectationModel(
        valid_categories=[ColumnCategories(column="status", valid_values=["active", "inactive"])]
    )
    findings, _ = evaluate([obs], exp)
    assert len(findings) == 1 and findings[0].severity == "warn"
    assert "ZZZ" in findings[0].observed


# --------------------------------------------------------------------------- #
# Duplicates / leakage / collinearity / drift
# --------------------------------------------------------------------------- #
def test_key_duplicates_are_critical():
    obs = StructuralObs(
        id="o",
        payload={"duplicates": {"full_row_dup_count": 0, "key_level": [{"key": "customer_id", "dup_count": 412}]}},
    )
    findings, _ = evaluate([obs], ExpectationModel())
    assert [f.severity for f in findings] == ["critical"]


def test_target_leakage_is_critical():
    obs = BivariateObs(
        id="o",
        payload={"target_relationships": {"feature_target_correlations": [{"column": "leaky", "pearson_r_with_target": 0.99}]}},
    )
    findings, _ = evaluate([obs], ExpectationModel())
    assert [f.severity for f in findings] == ["critical"]


def test_high_correlation_is_warn():
    obs = BivariateObs(
        id="o",
        payload={"correlations": {"high_corr_pairs": [{"col1": "a", "col2": "b", "pearson_r": 0.91}]}},
    )
    findings, _ = evaluate([obs], ExpectationModel())
    assert [f.severity for f in findings] == ["warn"]


def test_drift_severity_passthrough():
    obs = DriftObs(
        id="o",
        payload={"status": "computed", "per_column": {"x": {"psi": 0.3, "severity": "critical"}, "y": {"psi": 0.01, "severity": "info"}}},
    )
    findings, _ = evaluate([obs], ExpectationModel())
    assert len(findings) == 1 and findings[0].severity == "critical"


# --------------------------------------------------------------------------- #
# Structural validity block (negatives / future dates / categories / hygiene)
# --------------------------------------------------------------------------- #
def test_validity_negatives_and_future_dates_are_warn():
    obs = StructuralObs(
        id="o",
        payload={"validity": [
            {"column": "age", "negatives": 7},
            {"column": "signup", "future_dates": 3},
        ]},
    )
    findings, _ = evaluate([obs], ExpectationModel())
    assert {f.column for f in findings} == {"age", "signup"}
    assert all(f.severity == "warn" for f in findings)


def test_validity_string_hygiene_is_info():
    obs = StructuralObs(
        id="o",
        payload={"validity": [{"column": "name", "string_hygiene": {"leading_trailing_whitespace": 4}}]},
    )
    findings, surprises = evaluate([obs], ExpectationModel())
    assert len(findings) == 1 and findings[0].severity == "info"
    assert surprises == []  # info never spawns a chase


def test_validity_out_of_range_not_double_counted():
    """Range breaches are owned by the univariate rule; structural skips them."""
    obs = StructuralObs(id="o", payload={"validity": [{"column": "age", "out_of_range": 9}]})
    findings, _ = evaluate([obs], ExpectationModel())
    assert findings == []


# --------------------------------------------------------------------------- #
# Surprise emission bar
# --------------------------------------------------------------------------- #
def test_info_findings_emit_no_surprise():
    obs = FirstContactObs(id="o", payload={"dtypes": {"age": "float64"}})
    exp = ExpectationModel(expected_dtypes=[ColumnDtype(column="age", expected_dtype="int")])
    findings, surprises = evaluate([obs], exp)
    assert [f.severity for f in findings] == ["info"]
    assert surprises == []


def test_critical_finding_spawns_linked_surprise():
    obs = StructuralObs(
        id="o", payload={"duplicates": {"key_level": [{"key": "id", "dup_count": 5}]}}
    )
    findings, surprises = evaluate([obs], ExpectationModel())
    assert len(surprises) == 1
    assert surprises[0].finding_id == findings[0].id


# --------------------------------------------------------------------------- #
# Graph adapter
# --------------------------------------------------------------------------- #
def test_make_evaluation_partial_shape():
    obs = StructuralObs(
        id="o", payload={"duplicates": {"key_level": [{"key": "id", "dup_count": 5}]}}
    )
    partial = make_evaluation_partial([obs], ExpectationModel())
    assert set(partial) == {"ledger", "open_surprises"}
    assert partial["ledger"] and partial["open_surprises"]
