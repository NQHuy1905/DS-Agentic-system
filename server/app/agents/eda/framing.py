"""Framing agent: objective/grain/provenance + ExpectationModel priors.

Guard G_frame — PII redaction (heuristic regex) on sample rows before any LLM prompt;
prompt-injection defense (delimiters + control-char stripping on dataset-derived strings);
schema sanity (validate ExpectationModel against light profile, reject+retry on mismatch).
LLM injection: node signature (state, config); llm = config["configurable"]["llm"].
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from pydantic import BaseModel

from app.core.prompt_loader import render_prompt
from app.models.eda_schemas import EDAState, ExpectationModel

_PII: list[re.Pattern] = [
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),        # email
    re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),                              # SSN
    re.compile(r"\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"),  # US phone
    re.compile(r"\+\d[\d\s\-.]{6,}\d"),                                          # international phone
]
_REDACTED = "[REDACTED]"

# Credit-card numbers are stored in many separator formats (contiguous, spaced,
# dashed), so a fixed-shape regex misses real PANs. Match any 13-19 digit run
# (separators allowed) and confirm with the Luhn checksum to avoid redacting
# unrelated long numbers. Run before phone matching so a full PAN is redacted
# as one unit rather than partially consumed by the phone pattern.
_CC_CANDIDATE = re.compile(r"\b\d(?:[ \-]?\d){12,18}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact_credit_cards(value: str) -> str:
    def repl(m: re.Match) -> str:
        digits = re.sub(r"[ \-]", "", m.group(0))
        return _REDACTED if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)

    return _CC_CANDIDATE.sub(repl, value)
_MAX_RETRIES = 3
_NUMERIC_DTYPES = ("int", "float", "double", "numeric", "number")


class _FramingMeta(BaseModel):
    objective: str
    grain: str
    provenance: str


def _redact_pii(value: str) -> str:
    value = _redact_credit_cards(value)
    for pat in _PII:
        value = pat.sub(_REDACTED, value)
    return value


def _sanitize_col_name(name: str) -> str:
    s = re.sub(r"[\r\n\t]", " ", name)
    return "".join(c for c in s if not unicodedata.category(c).startswith("C") or c == " ").strip()


def _sanitize_value(value: Any) -> str:
    s = re.sub(r"[\r\n\t]", " ", "" if value is None else str(value))
    s = "".join(c for c in s if not unicodedata.category(c).startswith("C") or c == " ")
    return _redact_pii(s)


def _build_light_profile(dataset_meta: dict, sample: list[dict]) -> dict:
    """Sanitize column names + PII-redact sample values."""
    return {
        "column_names": [_sanitize_col_name(c) for c in dataset_meta.get("column_names", [])],
        "dtypes": dataset_meta.get("dtypes", {}),
        "sanitized_sample": [
            {_sanitize_col_name(k): _sanitize_value(v) for k, v in row.items()}
            for row in sample
        ],
    }


def _validate_expectations(em: ExpectationModel, lp: dict) -> list[str]:
    """Return error strings for hallucinated or type-incompatible columns."""
    valid = set(lp["column_names"]) | set(lp["dtypes"])
    ok = lambda col: col in valid or _sanitize_col_name(col) in lp["column_names"]  # noqa: E731
    dtype = lambda col: lp["dtypes"].get(col) or lp["dtypes"].get(_sanitize_col_name(col), "")  # noqa: E731
    errs: list[str] = []
    for item in em.expected_dtypes:
        if not ok(item.column): errs.append(f"Hallucinated column in expected_dtypes: {item.column!r}")
    for item in em.ranges:
        if not ok(item.column):
            errs.append(f"Hallucinated column in ranges: {item.column!r}")
        elif (d := dtype(item.column)) and not any(t in d for t in _NUMERIC_DTYPES):
            errs.append(f"Range for non-numeric column {item.column!r} (dtype={d!r})")
    for item in em.null_priors:
        if not ok(item.column): errs.append(f"Hallucinated column in null_priors: {item.column!r}")
    for item in em.valid_categories:
        if not ok(item.column): errs.append(f"Hallucinated column in valid_categories: {item.column!r}")
    return errs


def _priors_prompt(lp: dict, objective: str) -> str:
    # Data-derived strings are built from the already-sanitized/redacted light
    # profile before substitution; the template lives in config/prompts/framing.yaml.
    cols = ", ".join(f'"{c}"' for c in lp["column_names"])
    dtypes = "\n".join(f"  - {_sanitize_col_name(c)}: {d}" for c, d in lp["dtypes"].items())
    rows = "".join(
        f"  [ROW {i}] {', '.join(f'{k}={v!r}' for k, v in r.items())}\n"
        for i, r in enumerate(lp["sanitized_sample"][:5])
    )
    return render_prompt("framing", "priors", objective=objective, columns=cols, dtypes=dtypes, rows=rows)


def _meta_prompt(lp: dict, objective: str) -> str:
    cols = ", ".join(f'"{c}"' for c in lp["column_names"])
    return render_prompt("framing", "meta", objective=objective, columns=cols)


def build_framing(
    llm: Any,
    dataset_meta: dict,
    sample: list[dict],
    user_objective: str,
) -> dict:
    """Return {objective, grain, provenance, expectations: ExpectationModel}.

    Calls llm.with_structured_output(ExpectationModel) for priors (with G_frame
    validation + bounded retries), then .with_structured_output(_FramingMeta) for
    objective/grain/provenance. Sample rows are PII-redacted before any prompt.

    Raises ValueError when ExpectationModel fails sanity-check after _MAX_RETRIES.
    """
    lp = _build_light_profile(dataset_meta, sample)
    priors_llm = llm.with_structured_output(ExpectationModel)
    base_prompt = _priors_prompt(lp, user_objective)

    last_err, em = "", None
    for attempt in range(_MAX_RETRIES):
        prompt = (
            base_prompt + render_prompt("framing", "priors_retry_suffix", last_err=last_err)
            if attempt and last_err else base_prompt
        )
        result: ExpectationModel = priors_llm.invoke(prompt)
        errs = _validate_expectations(result, lp)
        if not errs:
            em = result
            break
        last_err = "; ".join(errs)

    if em is None:
        raise ValueError(f"ExpectationModel rejected after {_MAX_RETRIES} retries: {last_err}")

    meta: _FramingMeta = llm.with_structured_output(_FramingMeta).invoke(_meta_prompt(lp, user_objective))
    return {"objective": meta.objective, "grain": meta.grain, "provenance": meta.provenance, "expectations": em}


def framing(state: EDAState, config: dict) -> dict:
    """LangGraph node — reads llm from config["configurable"]["llm"].

    Loads dataset via dataset_ref, extracts light profile (dtypes + bounded sample),
    delegates to build_framing, writes objective/grain/provenance/expectations to state.
    """
    import numpy as np
    from app.ingestion.loader import load_dataframe

    llm = config["configurable"]["llm"]
    df = load_dataframe(state["dataset_ref"])
    rng = np.random.default_rng(42)
    n = min(5, len(df))
    rand_idx = sorted(rng.choice(len(df), size=n, replace=False).tolist())
    meta = {
        "column_names": list(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "shape": {"rows": len(df), "cols": len(df.columns)},
    }
    sample = df.head(5).fillna("").astype(str).to_dict(orient="records")
    sample += df.iloc[rand_idx].fillna("").astype(str).to_dict(orient="records")

    r = build_framing(llm, meta, sample, state.get("objective", ""))  # type: ignore[arg-type]
    return {"objective": r["objective"], "grain": r["grain"], "provenance": r["provenance"], "expectations": r["expectations"]}
