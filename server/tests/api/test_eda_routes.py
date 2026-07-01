"""Offline HTTP smoke tests for the EDA routes — validation + wiring only, no
LLM calls or real runs (those need network / a live model).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_LLM = {"provider": "openai", "model": "gpt-4o", "api_key": "test-key"}


def test_health_ok():
    assert client.get("/health").json() == {"status": "ok"}


def test_llmconfig_accepts_client_camelcase_api_key():
    """The TS client sends `apiKey`; the schema must accept it and snake_case."""
    from app.models.workflow_schemas import LLMConfig

    camel = LLMConfig.model_validate({"provider": "openai", "model": "gpt-4o", "apiKey": "k"})
    snake = LLMConfig.model_validate({"provider": "openai", "model": "gpt-4o", "api_key": "k"})
    assert camel.api_key == "k" and snake.api_key == "k"


def test_run_rejects_malformed_dataset_ref():
    # Traversal-looking ref fails the ref format check before any LLM/run work.
    r = client.post(
        "/api/v1/eda/run",
        json={"llm_config": _LLM, "dataset_ref": "../../etc/passwd", "objective": ""},
    )
    assert r.status_code == 400


def test_report_rejects_short_ref():
    assert client.get("/api/v1/eda/report/zzzz").status_code == 400


def test_report_missing_returns_404():
    # Well-formed but non-existent ref -> not found (containment + is_file check).
    r = client.get("/api/v1/eda/report/" + "a" * 32)
    assert r.status_code == 404


def test_upload_route_is_mounted():
    # No multipart body -> FastAPI validation error, proving the route exists.
    r = client.post("/api/v1/eda/upload")
    assert r.status_code == 422


def test_stream_unknown_run_returns_404():
    assert client.get("/api/v1/eda/stream/" + "f" * 32).status_code == 404


def test_resume_unknown_run_returns_404():
    r = client.post(
        "/api/v1/eda/resume/" + "f" * 32,
        json={"checkpoint": "contract", "response": {}},
    )
    assert r.status_code == 404
