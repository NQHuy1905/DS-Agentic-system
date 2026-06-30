"""SandboxResult — the single return type for sandbox code execution."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class SandboxResult(BaseModel):
    ok: bool
    value: Optional[Any] = None
    stdout: str = ""
    error: Optional[str] = None
    duration: float = 0.0
