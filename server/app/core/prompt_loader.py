"""Load node prompt templates from per-node YAML files.

Prompts live in `server/config/prompts/{node}.yaml` so they can be traced and
edited without touching Python. Each file maps a prompt key -> template string
containing `{placeholder}` tokens.

Rendering rule (security): callers pass ONLY pre-sanitized / PII-redacted values.
Substitution is a single left-to-right pass over the template (not str.format and
not repeated replace), so a value that itself contains a `{token}` cannot be
re-expanded into another placeholder, and stray braces a user leaves in a
template are left untouched rather than raising. This keeps the data-sanitization
+ delimiter guards in the calling code fully effective.

Caching: each file is parsed once and cached. Edit a YAML then restart the
server to pick up changes.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_TOKEN = re.compile(r"\{(\w+)\}")

# server/app/core/prompt_loader.py -> parents[2] == server/
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"


@lru_cache(maxsize=None)
def _load_node(node: str) -> dict:
    path = _PROMPTS_DIR / f"{node}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found for node {node!r}: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Prompt file {path} must parse to a mapping of key -> template")
    return data


def render_prompt(node: str, key: str, **ctx: object) -> str:
    """Return the `key` template from `{node}.yaml` with `{name}` tokens filled.

    Raises KeyError if the key is absent so a missing prompt fails loudly rather
    than silently sending an empty string to the LLM.
    """
    templates = _load_node(node)
    if key not in templates:
        raise KeyError(f"Prompt key {key!r} not found in {node}.yaml (have: {sorted(templates)})")
    text = str(templates[key])
    # Single pass: a token is replaced from ctx once; substituted text is not
    # re-scanned, and an unknown token is left literal (never raises on a template
    # the user edited to include stray braces).
    return _TOKEN.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), text)
