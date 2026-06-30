"""SSE event contract for the EDA workflow (agent -> UI).

Each event carries a monotonic `id` so the stream endpoint can honor
`Last-Event-ID` on reconnect/resume and replay the tail from the durable
per-run event buffer — without it, findings emitted during a connection gap
or after a human-checkpoint resume are silently lost.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.models.eda_schemas import Finding


class PhaseStartEvent(BaseModel):
    type: Literal["phase_start"] = "phase_start"
    id: int
    phase: str


class FindingEvent(BaseModel):
    type: Literal["finding"] = "finding"
    id: int
    finding: Finding


class InterruptEvent(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    id: int
    checkpoint: Literal["contract", "review"]
    payload: dict = {}


class ReportReadyEvent(BaseModel):
    type: Literal["report_ready"] = "report_ready"
    id: int
    report_url: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    id: int
    message: str


EDAEvent = Annotated[
    Union[
        PhaseStartEvent,
        FindingEvent,
        InterruptEvent,
        ReportReadyEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]


def serialize(event: BaseModel) -> str:
    """Render an event as an SSE frame with an `id:` line for Last-Event-ID."""
    return f"id: {event.id}\ndata: {json.dumps(event.model_dump())}\n\n"
