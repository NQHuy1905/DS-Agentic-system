"""Offline tests for framing.py — no network, deterministic.

G_frame/PII     — emails/phones/SSNs/credit-cards stripped before prompts.
G_frame/sanity  — hallucinated columns trigger reject + retry.
G_frame/retries — ValueError after _MAX_RETRIES exhausted.
Provider smoke  — ExpectationModel JSON schema has no additionalProperties/prefixItems.
"""
from __future__ import annotations

import json
from typing import Callable

import pytest

from app.agents.eda.framing import (
    _build_light_profile, _redact_pii, _sanitize_col_name, build_framing,
)
from app.models.eda_schemas import (
    ColumnCategories, ColumnDtype, ColumnNullPrior, ColumnRange, ExpectationModel,
)

# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
_META = {
    "column_names": ["age", "name", "status"],
    "dtypes": {"age": "int64", "name": "object", "status": "object"},
}
_SAMPLE = [{"age": "25", "name": "Alice", "status": "active"}, {"age": "30", "name": "Bob", "status": "inactive"}]


def _good_em() -> ExpectationModel:
    return ExpectationModel(
        expected_dtypes=[ColumnDtype(column="age", expected_dtype="int64"), ColumnDtype(column="name", expected_dtype="object"), ColumnDtype(column="status", expected_dtype="object")],
        ranges=[ColumnRange(column="age", min=0.0, max=120.0)],
        null_priors=[ColumnNullPrior(column="age", expected_null_rate=0.02), ColumnNullPrior(column="name", expected_null_rate=0.0)],
        valid_categories=[ColumnCategories(column="status", valid_values=["active", "inactive"])],
        row_magnitude=1000, notes="Standard customer snapshot",
    )


class _FakeMeta:
    objective = "Detect customer churn"
    grain = "One row = one customer"
    provenance = "CRM export via nightly ETL"


def _make_llm(factory: Callable[[int], ExpectationModel], captured: list | None = None) -> object:
    """Stub LLM; factory(attempt_index) drives expectations per retry."""
    prompts = captured if captured is not None else []

    class _BoundEM:
        def __init__(self) -> None:
            self._n = 0
        def invoke(self, prompt: str) -> ExpectationModel:
            prompts.append(("expectations", prompt))
            r = factory(self._n); self._n += 1; return r

    class _BoundMeta:
        def invoke(self, prompt: str) -> _FakeMeta:
            prompts.append(("meta", prompt)); return _FakeMeta()

    class _FakeLLM:
        def with_structured_output(self, schema: type) -> object:
            return _BoundEM() if schema is ExpectationModel else _BoundMeta()

    return _FakeLLM()


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #
def test_happy_path_returns_full_result() -> None:
    result = build_framing(_make_llm(lambda _: _good_em()), _META, _SAMPLE, "Detect churn")
    assert result["objective"] and result["grain"] and result["provenance"]
    em: ExpectationModel = result["expectations"]
    assert len(em.expected_dtypes) > 0 and len(em.ranges) > 0
    assert len(em.null_priors) > 0 and len(em.valid_categories) > 0
    assert em.row_magnitude and em.notes


# --------------------------------------------------------------------------- #
# G_frame / PII redaction                                                      #
# --------------------------------------------------------------------------- #
_PII_META = {
    "column_names": ["age", "email", "phone", "ssn", "cc"],
    "dtypes": {"age": "int64", "email": "object", "phone": "object", "ssn": "object", "cc": "object"},
}
_PII_EM = ExpectationModel(
    expected_dtypes=[ColumnDtype(column=c, expected_dtype="object") for c in ["age", "email", "phone", "ssn", "cc"]],
    null_priors=[ColumnNullPrior(column="age", expected_null_rate=0.0)],
    row_magnitude=5, notes="pii test",
)


def test_pii_redacted_before_prompt() -> None:
    pii_row = {"age": "28", "email": "john@example.com", "phone": "555-123-4567", "ssn": "123-45-6789", "cc": "4111111111111111"}
    captured: list = []
    build_framing(_make_llm(lambda _: _PII_EM, captured), _PII_META, [pii_row], "PII test")
    all_text = " ".join(t for _, t in captured)
    for leaked in ("john@example.com", "555-123-4567", "123-45-6789", "4111111111111111"):
        assert leaked not in all_text, f"PII leaked into prompt: {leaked}"
    assert "[REDACTED]" in all_text


# --------------------------------------------------------------------------- #
# G_frame / schema sanity — retry logic                                        #
# --------------------------------------------------------------------------- #
def test_hallucinated_column_triggers_retry() -> None:
    calls = [0]
    def factory(attempt: int) -> ExpectationModel:
        calls[0] += 1
        if attempt == 0:
            return ExpectationModel(expected_dtypes=[ColumnDtype(column="HALLUCINATED", expected_dtype="float64")], row_magnitude=10, notes="bad")
        return _good_em()

    result = build_framing(_make_llm(factory), _META, _SAMPLE, "Test retry")
    assert calls[0] == 2
    assert "HALLUCINATED" not in {e.column for e in result["expectations"].expected_dtypes}


def test_retry_prompt_includes_rejection_reason() -> None:
    captured: list = []
    def factory(attempt: int) -> ExpectationModel:
        if attempt == 0:
            return ExpectationModel(expected_dtypes=[ColumnDtype(column="GHOST", expected_dtype="float64")], row_magnitude=1, notes="bad")
        return _good_em()

    build_framing(_make_llm(factory, captured), _META, _SAMPLE, "Retry reason")
    em_prompts = [t for kind, t in captured if kind == "expectations"]
    assert len(em_prompts) == 2
    assert "PREVIOUS ATTEMPT REJECTED" in em_prompts[1]


def test_max_retries_exhausted_raises_value_error() -> None:
    bad_em = ExpectationModel(expected_dtypes=[ColumnDtype(column="ALWAYS_BAD", expected_dtype="float64")], row_magnitude=1, notes="bad")
    with pytest.raises(ValueError, match="rejected after"):
        build_framing(_make_llm(lambda _: bad_em), _META, _SAMPLE, "Force exhaustion")


# --------------------------------------------------------------------------- #
# Provider smoke                                                               #
# --------------------------------------------------------------------------- #
def test_expectation_model_schema_gemini_safe() -> None:
    """ExpectationModel JSON schema must not contain additionalProperties/prefixItems."""
    s = json.dumps(ExpectationModel.model_json_schema())
    assert "additionalProperties" not in s, "Gemini rejects 'additionalProperties' in structured-output schema"
    assert "prefixItems" not in s, "Gemini rejects 'prefixItems' in structured-output schema"


# --------------------------------------------------------------------------- #
# G_frame building-block unit tests                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,token", [
    ("user@example.com", "user@example.com"),
    ("call 555-123-4567", "555-123-4567"),
    ("SSN: 123-45-6789", "123-45-6789"),
    ("card 4111111111111111", "4111111111111111"),
    ("card 4111 1111 1111 1111", "4111 1111 1111 1111"),   # spaced PAN
    ("card 4111-1111-1111-1111", "4111-1111-1111-1111"),   # dashed PAN
    ("ring +44 20 7946 0958", "+44 20 7946 0958"),          # international phone
])
def test_redact_pii_patterns(raw: str, token: str) -> None:
    assert token not in _redact_pii(raw)
    assert "[REDACTED]" in _redact_pii(raw)


def test_non_card_long_digit_run_is_not_redacted() -> None:
    """Luhn gate avoids over-redacting unrelated long numbers (e.g. an order id)."""
    raw = "order 1234567890123456"  # fails Luhn -> not a card
    assert _redact_pii(raw) == raw


def test_sanitize_col_name_strips_control_chars() -> None:
    assert "\n" not in _sanitize_col_name("col\nINJECT")
    assert "\r" not in _sanitize_col_name("col\rname")
    assert _sanitize_col_name("good_col") == "good_col"


def test_build_light_profile_sanitizes_sample() -> None:
    meta = {"column_names": ["id", "email\nINJECT"], "dtypes": {"id": "int64", "email\nINJECT": "object"}}
    profile = _build_light_profile(meta, [{"id": "1", "email\nINJECT": "bad@actor.com"}])
    assert all("\n" not in c for c in profile["column_names"])
    assert all("bad@actor.com" not in v for row in profile["sanitized_sample"] for v in row.values())
