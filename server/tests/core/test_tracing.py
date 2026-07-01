"""Tracing config: no-op when off, masks data when on."""
from __future__ import annotations

from app.core.tracing import configure_tracing


def test_tracing_off_is_noop(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    assert configure_tracing() is False


def test_tracing_on_defaults_masking(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGCHAIN_HIDE_INPUTS", raising=False)
    monkeypatch.delenv("LANGCHAIN_HIDE_OUTPUTS", raising=False)
    import os
    assert configure_tracing() is True
    assert os.environ["LANGCHAIN_HIDE_INPUTS"] == "true"
    assert os.environ["LANGCHAIN_HIDE_OUTPUTS"] == "true"


def test_tracing_on_respects_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "1")
    monkeypatch.setenv("LANGCHAIN_HIDE_INPUTS", "false")
    import os
    configure_tracing()
    assert os.environ["LANGCHAIN_HIDE_INPUTS"] == "false"  # setdefault must not override
