"""Tests for the YAML prompt loader."""
from __future__ import annotations

import pytest

from app.core.prompt_loader import render_prompt


def test_renders_known_placeholders():
    out = render_prompt("framing", "meta", objective="predict churn", columns='"a", "b"')
    assert "predict churn" in out
    assert '"a", "b"' in out
    assert "{objective}" not in out and "{columns}" not in out


def test_delimiters_preserved_in_template():
    out = render_prompt("framing", "priors", objective="x", columns="c", dtypes="d", rows="r")
    assert "[DATASET_DATA]" in out and "[/DATASET_DATA]" in out


def test_retry_suffix_carries_reason():
    out = render_prompt("framing", "priors_retry_suffix", last_err="bad column foo")
    assert "PREVIOUS ATTEMPT REJECTED" in out
    assert "bad column foo" in out


def test_missing_key_raises_loudly():
    with pytest.raises(KeyError):
        render_prompt("framing", "no_such_key")


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        render_prompt("no_such_node", "any")


def test_brace_in_value_is_inert():
    """A substituted value containing braces must not create a new placeholder."""
    out = render_prompt("framing", "meta", objective="{columns}", columns="SAFE")
    # The injected {columns} token stays literal; only the real placeholder filled.
    assert "SAFE" in out
    assert out.count("SAFE") == 1  # value's literal {columns} was not expanded


def test_synthesizer_unverified_instruction_loads():
    out = render_prompt("synthesizer", "unverified_instruction")
    assert "[unverified]" in out
