"""EDA Synthesizer. Guard G_synth:
- Tables built deterministically from ledger — grounded by construction.
- LLM prose only; each prose section carries an advisory notice.
- Propose-only: no data mutations here; destructive ops gate at the human-review step.
"""
from __future__ import annotations

from typing import Any

import app.ingestion.storage as _storage
from app.models.eda_schemas import EDAState, ExpectationModel, Finding

# Severity sort key: critical=0 < warn=1 < info=2 (ascending = highest-priority first)
_SEV_ORDER: dict[str, int] = {"critical": 0, "warn": 1, "info": 2}

# Fixed section headings — stable identifiers for tests + document navigation.
SECTION_HEADINGS: list[str] = [
    "## 1. Objective & Grain",
    "## 2. Data Dictionary",
    "## 3. Issue Log",
    "## 4. Decisions & Rationale",
    "## 5. Pipeline Recommendations",
    "## 6. Caveats",
]

_ADVISORY = (
    "> **Advisory:** LLM-generated prose below. Claims not directly supported by a "
    "listed finding are prefixed *[unverified]* and should be treated as "
    "low-confidence. Deterministic tables are grounded by construction."
)

# Appended to prose prompts so the LLM actually performs the [unverified] marking
# the advisory promises — keeping the guard claim and behavior in sync.
_UNVERIFIED_INSTRUCTION = (
    "Prefix any statement not directly supported by a listed finding with "
    "'[unverified]'."
)


def _esc(text: Any) -> str:
    """Escape Markdown table-breaking characters in a cell value."""
    return str(text).replace("|", r"\|").replace("\n", " ")


# --- Deterministic table builders (cannot omit or invent findings) ---

def _build_data_dictionary(state: EDAState) -> str:
    """Data dictionary from expectations + ledger observations. Deterministic."""
    expectations: ExpectationModel | None = state.get("expectations")  # type: ignore[assignment]
    ledger: list[Finding] = state.get("ledger", [])  # type: ignore[assignment]
    col_notes: dict[str, list[str]] = {}
    for f in ledger:
        if f.column:
            col_notes.setdefault(f.column, []).append(_esc(f.description[:80]))
    rows: list[str] = []
    if expectations:
        for cd in expectations.expected_dtypes:
            notes = "; ".join(col_notes.get(cd.column, [])) or "—"
            rows.append(f"| `{_esc(cd.column)}` | `{_esc(cd.expected_dtype)}` | {notes} |")
    if not rows:
        return "_No column expectations registered._"
    header = "| Column | Expected Type | Findings / Observations |"
    sep    = "|--------|--------------|-------------------------|"
    return "\n".join([header, sep, *rows])


def _build_issue_log(ledger: list[Finding]) -> str:
    """All findings sorted critical→warn→info. Grounded by construction."""
    if not ledger:
        return "_No findings recorded._"
    ordered = sorted(ledger, key=lambda f: _SEV_ORDER.get(f.severity, 99))
    header = "| ID | Severity | Column | Description | Evidence | Root Cause | Decision |"
    sep    = "|----|----------|--------|-------------|----------|------------|----------|"
    rows   = [
        f"| {f.id} | **{f.severity}** | {_esc(f.column or '—')} | {_esc(f.description[:100])} "
        f"| {_esc(f.evidence_ref)} | {_esc((f.root_cause or '—')[:80])} | {_esc((f.decision or 'pending')[:80])} |"
        for f in ordered
    ]
    return "\n".join([header, sep, *rows])


def _build_decisions_rationale(ledger: list[Finding]) -> str:
    """Decisions table from Finding.decision fields. Deterministic."""
    decided = [f for f in ledger if f.decision]
    if not decided:
        return "_No explicit decisions recorded in ledger._"
    header = "| Finding ID | Severity | Decision / Rationale |"
    sep    = "|------------|----------|--------------------|"
    rows   = [f"| {f.id} | **{f.severity}** | {_esc(f.decision)} |" for f in decided]
    return "\n".join([header, sep, *rows])


def _prose(llm: Any, prompt: str) -> str:
    """Invoke LLM for a narrative section; return plain text content."""
    resp = llm.invoke(prompt)
    return (resp.content if hasattr(resp, "content") else str(resp)).strip()

# --- Public API ---

def synthesize(llm: Any, state: EDAState) -> dict[str, str]:
    """Build EDA report from state.ledger + expectations; persist to storage.

    Returns: {"markdown": <str>, "report_ref": <UUID4 storage ref>}
    G_synth: tables deterministic/grounded; LLM prose advisory-flagged; propose-only.
    """
    ledger: list[Finding] = state.get("ledger", [])  # type: ignore[assignment]
    objective  = state.get("objective", "Not specified")
    grain      = state.get("grain", "Not specified")
    provenance = state.get("provenance", "Not specified")
    run_id     = state.get("run_id", "unknown")
    n_crit = sum(1 for f in ledger if f.severity == "critical")
    n_warn = sum(1 for f in ledger if f.severity == "warn")
    n_info = sum(1 for f in ledger if f.severity == "info")

    obj_prose = _prose(
        llm,
        f"Summarize the EDA objective and grain in 2-3 sentences for a technical report.\n"
        f"Objective: {objective}\nGrain: {grain}\nProvenance: {provenance}\n"
        "Be concise. Do not invent facts beyond what is provided.",
    )
    findings_summary = "\n".join(
        f"- {f.id} | {f.severity} | {f.description[:100]}" for f in ledger
    )
    recs_prose = _prose(
        llm,
        "Propose pipeline recommendations (transforms, imputation, drops) based on findings.\n"
        "Proposals only — do NOT execute anything. Reference finding IDs.\n"
        f"Critical: {n_crit}, Warn: {n_warn}, Info: {n_info}.\n"
        f"Findings:\n{findings_summary}\nLimit: 400 words.\n" + _UNVERIFIED_INSTRUCTION,
    )
    caveats_prose = _prose(
        llm,
        f"List 3-5 caveats and limitations for this EDA report.\n"
        f"run_id={run_id}, total findings={len(ledger)}, "
        f"critical={n_crit}, warn={n_warn}, info={n_info}.\n"
        "Be honest about what the analysis cannot determine.\n" + _UNVERIFIED_INSTRUCTION,
    )

    parts = [
        f"# EDA Report — run `{run_id}`", "",
        "---", "",
        SECTION_HEADINGS[0], "",
        _ADVISORY, "",
        obj_prose, "",
        "---", "",
        SECTION_HEADINGS[1], "",
        "*Deterministic — derived from column expectations + ledger observations.*", "",
        _build_data_dictionary(state), "",
        "---", "",
        SECTION_HEADINGS[2], "",
        f"*All {len(ledger)} findings ordered by severity (critical→warn→info). "
        "Grounded by construction — every ledger entry is listed.*", "",
        _build_issue_log(ledger), "",
        "---", "",
        SECTION_HEADINGS[3], "",
        "*Deterministic — derived from `Finding.decision` fields.*", "",
        _build_decisions_rationale(ledger), "",
        "---", "",
        SECTION_HEADINGS[4], "",
        _ADVISORY, "",
        "> **Propose-only:** Advisory text only. No data is mutated by the Synthesizer.",
        "> Destructive ops require explicit sign-off at the human-review gate.", "",
        recs_prose, "",
        "---", "",
        SECTION_HEADINGS[5], "",
        _ADVISORY, "",
        caveats_prose, "",
    ]
    md = "\n".join(parts)
    report_ref = _storage.save_upload(md.encode("utf-8"), f"eda_report_{run_id}.md")
    return {"markdown": md, "report_ref": report_ref}


def synthesizer(state: EDAState, config: dict) -> dict:  # type: ignore[type-arg]
    """LangGraph node: synthesize EDA report from accumulated ledger.

    LLM injected via config["configurable"]["llm"] — the LLM-injection contract.
    Returns partial state update: {"report": report_ref}.

    Download-route integration seam: the route resolves the report by calling
    storage.path_for(report_ref) to get the filesystem path, then streams
    the file as Content-Type: text/markdown.
    """
    llm = config["configurable"]["llm"]
    result = synthesize(llm, state)
    return {"report": result["report_ref"]}
