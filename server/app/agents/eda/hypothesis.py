"""Hypothesis engine: chase one surprising finding by generating candidate
causes, probing each with sandboxed pandas code, and recording the confirmed
root cause (or "unexplained") back into the ledger.

Guards:
- Every probe runs ONLY in the sandbox (`run_code`); no direct exec. The sandbox
  is the containment backstop for generated code, not the sole barrier.
- Data-derived strings reaching the LLM (the surprise text, column names, the
  candidate cause) are untrusted: they are PII-redacted, control-char stripped,
  and have the data delimiters neutralised before being wrapped in the prompt's
  delimiter block, so a poisoned column name/value cannot break out of the block
  or steer probe generation.
- The chase is bounded: at most one surprise per node invocation (indexed by the
  hypothesis budget counter) and a small cap on probes per surprise.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel

# Reuse the shared redaction/sanitisation helpers so the logic lives in one place.
from app.agents.eda.framing import _redact_pii, _sanitize_col_name
from app.core.prompt_loader import render_prompt
from app.ingestion import storage
from app.models.eda_schemas import EDAState, Finding
from app.sandbox.executor import run_code

_MAX_PROBES_PER_CHASE = 4

# Matches the prompt's data-delimiter markers; stripped from untrusted text so a
# value like "]... ignore instructions" cannot close the block early.
_DELIM_RE = re.compile(r"\[/?DATASET_DATA\]", re.IGNORECASE)


def _safe_text(text: str) -> str:
    """PII-redact, strip control chars, and neutralise delimiter markers in an
    untrusted, data-derived string before it enters an LLM prompt."""
    s = _redact_pii(str(text))
    s = re.sub(r"[\r\n\t]", " ", s)
    return _DELIM_RE.sub("", s)


class _CauseList(BaseModel):
    causes: list[str]


class _ProbeCode(BaseModel):
    code: str


def _column_context(state: EDAState) -> str:
    exp = state.get("expectations")  # type: ignore[call-overload]
    cols = [cd.column for cd in exp.expected_dtypes] if exp is not None else []
    return ", ".join(_sanitize_col_name(c) for c in cols) or "(unknown)"


def hypothesize(llm: Any, question: str, columns: str) -> list[str]:
    prompt = render_prompt("hypothesis", "hypothesize", question=_safe_text(question), columns=columns)
    return llm.with_structured_output(_CauseList).invoke(prompt).causes


def design_probe(llm: Any, cause: str, columns: str) -> str:
    prompt = render_prompt("hypothesis", "design_probe", cause=_safe_text(cause), columns=columns)
    return llm.with_structured_output(_ProbeCode).invoke(prompt).code


def _confirms(result: Any) -> bool:
    """Interpret a probe's return value as a confirmation verdict.

    A probe must return a boolean. Only an explicit boolean (or a boolean that
    round-tripped through the sandbox's JSON serialisation as the string
    "true"/"false") counts. Everything else — a bare numeric result (note:
    numpy.float64/int64 are real Python numeric subclasses and pass through
    serialisation), None, or a stringified object — is treated as NOT confirmed,
    so a truthy-but-non-boolean value can never fabricate a root cause.
    """
    if not result.ok:
        return False
    value = result.value
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _find_finding(state: EDAState, finding_id: str) -> Optional[Finding]:
    for f in state.get("ledger", []):  # type: ignore[call-overload]
        if f.id == finding_id:
            return f
    return None


def hypothesis(state: EDAState, config: dict) -> dict:
    """Chase the next open surprise; append a resolution finding to the ledger.

    The hypothesis-budget counter doubles as the surprise index, so each
    invocation advances to a different surprise and the total number of chases is
    bounded by `budget.max_hypo_iters`.
    """
    llm = config["configurable"]["llm"]
    budget = state["budget"]
    surprises = state.get("open_surprises", [])  # type: ignore[call-overload]
    idx = budget.hypo_spent
    advanced = budget.model_copy(update={"hypo_spent": idx + 1})

    if idx >= len(surprises):
        return {"budget": advanced}  # nothing left to chase

    surprise = surprises[idx]
    origin = _find_finding(state, surprise.finding_id)
    columns = _column_context(state)
    df_path = str(storage.path_for(state["dataset_ref"]))

    confirmed: Optional[str] = None
    for cause in hypothesize(llm, surprise.question, columns)[:_MAX_PROBES_PER_CHASE]:
        code = design_probe(llm, cause, columns)
        result = run_code(code, df_path)          # sandboxed — never direct exec
        if _confirms(result):
            confirmed = cause
            break

    # Ground the resolution in the observation that produced the original finding.
    evidence_ref = origin.evidence_ref if origin is not None else surprise.finding_id
    column = origin.column if origin is not None else None
    if confirmed:
        resolution = Finding(
            id=uuid4().hex[:12], phase="hypothesis", column=column,
            observed=confirmed, expected=None, severity="info",
            description=f"Confirmed root cause for surprise {surprise.finding_id}: {confirmed}",
            evidence_ref=evidence_ref, root_cause=confirmed,
            decision="Address the identified root cause in the pipeline.",
        )
    else:
        resolution = Finding(
            id=uuid4().hex[:12], phase="hypothesis", column=column,
            observed=None, expected=None, severity="info",
            description=f"Surprise {surprise.finding_id} left unexplained after probing.",
            evidence_ref=evidence_ref, root_cause=None, decision=None,
        )
    return {"ledger": [resolution], "budget": advanced}
