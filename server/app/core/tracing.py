"""Optional LangSmith tracing configuration.

No-op unless LANGCHAIN_TRACING_V2 is truthy in the environment, so the app runs
identically with or without a LangSmith key (the langsmith client ships
transitively via langchain-core — nothing extra is pinned).

When tracing IS enabled, input/output masking is defaulted ON so uploaded-data
samples embedded in prompts are not shipped to or persisted in traces. The
user's api_key is never traced regardless: it lives only in the per-request LLM
client, never in graph state or the SQLite checkpoint.
"""
from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def configure_tracing() -> bool:
    """Enable data-masking defaults when tracing is on. Returns whether it is active."""
    if os.environ.get("LANGCHAIN_TRACING_V2", "").strip().lower() not in _TRUTHY:
        return False
    # Default masking ON; an operator can still override by setting these to false.
    os.environ.setdefault("LANGCHAIN_HIDE_INPUTS", "true")
    os.environ.setdefault("LANGCHAIN_HIDE_OUTPUTS", "true")
    return True
