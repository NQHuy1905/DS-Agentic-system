"""Evaluator: turn raw tool observations into severity-ranked, grounded Findings.

The mechanical diff (observed-vs-expected against the ExpectationModel) is pure
Python and fully deterministic — the spine of the node. An optional, thin LLM
pass can annotate descriptions/root-cause for borderline cases, but it is OFF by
default and never required for severity.

Grounding keystone: every Finding is built with `evidence_ref` = the
`Observation.id` that produced it. A finding literally cannot exist without
tracing back to a tool output (pydantic rejects construction otherwise).
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from app.models.eda_schemas import (
    ExpectationModel,
    Finding,
    Observation,
    Surprise,
)

# Explicit, tunable thresholds. Documented here so the rubric is auditable and
# adjustable in one place rather than scattered through the diff logic.
NULL_RATE_WARN_MULT = 2.0       # observed > 2x prior  -> warn
NULL_RATE_CRIT_MULT = 5.0       # observed > 5x prior  -> critical (if also high)
NULL_RATE_CRIT_FLOOR = 0.2      # ...and observed at least this absolute rate
LEAKAGE_ABS_R = 0.98            # |corr(feature,target)| at/above -> leakage signal
_NUMERIC = {"int", "float"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fid() -> str:
    return uuid4().hex[:12]


def _norm_dtype(raw: str) -> str:
    """Collapse a dtype label (pandas or expectation-side) to a coarse family."""
    d = str(raw).lower()
    if d.startswith("int") or d == "integer":
        return "int"
    if d.startswith("float") or d in {"double", "numeric", "number"}:
        return "float"
    if d in {"bool", "boolean"}:
        return "bool"
    if "date" in d or "time" in d:
        return "datetime"
    return "object"  # string / category / object / text


def _by_column(items: list[dict], key: str = "column") -> dict[str, dict]:
    return {row[key]: row for row in items if isinstance(row, dict) and key in row}


# --------------------------------------------------------------------------- #
# Per-tool diff rules. Each returns a list[Finding]; all carry evidence_ref=obs_id.
# --------------------------------------------------------------------------- #
def _eval_first_contact(payload: dict, exp: ExpectationModel, obs_id: str) -> list[Finding]:
    findings: list[Finding] = []
    observed_dtypes = payload.get("dtypes", {})
    for cd in exp.expected_dtypes:
        observed = observed_dtypes.get(cd.column)
        if observed is None:
            continue
        exp_fam, obs_fam = _norm_dtype(cd.expected_dtype), _norm_dtype(observed)
        if exp_fam == obs_fam:
            continue
        # int-vs-float is a benign numeric widening; anything else is a real breach.
        both_numeric = exp_fam in _NUMERIC and obs_fam in _NUMERIC
        findings.append(
            Finding(
                id=_fid(), phase="first_contact", column=cd.column,
                observed=observed, expected=cd.expected_dtype,
                severity="info" if both_numeric else "critical",
                description=f"dtype mismatch on '{cd.column}': expected {cd.expected_dtype}, observed {observed}",
                evidence_ref=obs_id,
            )
        )
    # row magnitude sanity (order-of-magnitude off -> worth recording)
    rows = payload.get("shape", {}).get("rows")
    if exp.row_magnitude and rows and (rows > exp.row_magnitude * 10 or rows * 10 < exp.row_magnitude):
        findings.append(
            Finding(
                id=_fid(), phase="first_contact", column=None,
                observed=rows, expected=exp.row_magnitude, severity="warn",
                description=f"row count {rows} differs by >1 order of magnitude from expected ~{exp.row_magnitude}",
                evidence_ref=obs_id,
            )
        )
    for tell in payload.get("parse_tells", []):
        if not isinstance(tell, dict):
            continue
        findings.append(
            Finding(
                id=_fid(), phase="first_contact", column=tell.get("column"),
                observed=tell.get("tell"), expected=None, severity="warn",
                description=f"parse tell on '{tell.get('column')}': {tell.get('tell')}",
                evidence_ref=obs_id,
            )
        )
    return findings


def _eval_structural(payload: dict, exp: ExpectationModel, obs_id: str) -> list[Finding]:
    findings: list[Finding] = []
    priors = {p.column: p.expected_null_rate for p in exp.null_priors}
    for row in payload.get("missingness", {}).get("per_column", []):
        col, observed = row.get("column"), row.get("null_rate")
        prior = priors.get(col)
        if prior is None or observed is None:
            continue
        # Guard against a zero prior: treat as a tiny floor so ratios stay finite.
        base = max(prior, 1e-6)
        if observed > base * NULL_RATE_CRIT_MULT and observed >= NULL_RATE_CRIT_FLOOR:
            sev = "critical"
        elif observed > base * NULL_RATE_WARN_MULT:
            sev = "warn"
        else:
            continue
        findings.append(
            Finding(
                id=_fid(), phase="structural", column=col,
                observed=observed, expected=prior, severity=sev,
                description=f"null rate on '{col}' is {observed:.3f} vs prior {prior:.3f}",
                evidence_ref=obs_id,
            )
        )
    dups = payload.get("duplicates", {})
    for kl in dups.get("key_level", []):
        if kl.get("dup_count", 0) > 0:
            findings.append(
                Finding(
                    id=_fid(), phase="structural", column=kl.get("key"),
                    observed=kl.get("dup_count"), expected=0, severity="critical",
                    description=f"key '{kl.get('key')}' has {kl.get('dup_count')} duplicate rows — grain/key integrity violation",
                    evidence_ref=obs_id,
                )
            )
    if dups.get("full_row_dup_count", 0) > 0:
        findings.append(
            Finding(
                id=_fid(), phase="structural", column=None,
                observed=dups["full_row_dup_count"], expected=0, severity="warn",
                description=f"{dups['full_row_dup_count']} fully duplicated rows",
                evidence_ref=obs_id,
            )
        )
    # Validity block. `out_of_range` is intentionally skipped here — declared-range
    # breaches are emitted from the univariate min/max rule with richer evidence,
    # so consuming both would double-list the same issue.
    for issue in payload.get("validity", []):
        col = issue.get("column")
        if issue.get("negatives"):
            findings.append(
                Finding(
                    id=_fid(), phase="structural", column=col,
                    observed=issue["negatives"], expected=None, severity="warn",
                    description=f"'{col}' has {issue['negatives']} negative values",
                    evidence_ref=obs_id,
                )
            )
        if issue.get("future_dates"):
            findings.append(
                Finding(
                    id=_fid(), phase="structural", column=col,
                    observed=issue["future_dates"], expected=None, severity="warn",
                    description=f"'{col}' has {issue['future_dates']} future-dated values",
                    evidence_ref=obs_id,
                )
            )
        if issue.get("invalid_categories"):
            findings.append(
                Finding(
                    id=_fid(), phase="structural", column=col,
                    observed=issue["invalid_categories"], expected=None, severity="warn",
                    description=f"'{col}' has {issue['invalid_categories']} values outside the declared category set",
                    evidence_ref=obs_id,
                )
            )
        hygiene = issue.get("string_hygiene")
        if hygiene:
            findings.append(
                Finding(
                    id=_fid(), phase="structural", column=col,
                    observed=hygiene, expected=None, severity="info",
                    description=f"'{col}' string hygiene issues: {hygiene}",
                    evidence_ref=obs_id,
                )
            )
    return findings


def _eval_univariate(payload: dict, exp: ExpectationModel, obs_id: str) -> list[Finding]:
    findings: list[Finding] = []
    numeric = payload.get("numeric", {})
    for rng in exp.ranges:
        stats = numeric.get(rng.column)
        if not stats or "min" not in stats or "max" not in stats:
            continue
        obs_min, obs_max = stats["min"], stats["max"]
        if obs_min < rng.min or obs_max > rng.max:
            findings.append(
                Finding(
                    id=_fid(), phase="univariate", column=rng.column,
                    observed=[obs_min, obs_max], expected=[rng.min, rng.max],
                    severity="critical",
                    description=f"'{rng.column}' observed range [{obs_min}, {obs_max}] breaches expected [{rng.min}, {rng.max}]",
                    evidence_ref=obs_id,
                )
            )
    categorical = payload.get("categorical", {})
    valid_map = {c.column: set(c.valid_values) for c in exp.valid_categories}
    for col, valid in valid_map.items():
        stats = categorical.get(col)
        if not stats:
            continue
        observed_vals = {item["value"] for item in stats.get("top_freq", []) if "value" in item}
        observed_vals |= set(stats.get("rare_cats_sample", []))
        unexpected = observed_vals - valid
        if unexpected:
            findings.append(
                Finding(
                    id=_fid(), phase="univariate", column=col,
                    observed=sorted(unexpected), expected=sorted(valid), severity="warn",
                    description=f"'{col}' has values outside the declared valid set: {sorted(unexpected)}",
                    evidence_ref=obs_id,
                )
            )
    return findings


def _eval_bivariate(payload: dict, exp: ExpectationModel, obs_id: str) -> list[Finding]:
    findings: list[Finding] = []
    rels = payload.get("target_relationships", {}).get("feature_target_correlations", [])
    for rel in rels:
        r = rel.get("pearson_r_with_target")
        if r is not None and abs(r) >= LEAKAGE_ABS_R:
            findings.append(
                Finding(
                    id=_fid(), phase="bivariate", column=rel.get("column"),
                    observed=r, expected=None, severity="critical",
                    description=f"'{rel.get('column')}' correlates with target at r={r} — possible target leakage",
                    evidence_ref=obs_id,
                )
            )
    for pair in payload.get("correlations", {}).get("high_corr_pairs", []):
        findings.append(
            Finding(
                id=_fid(), phase="bivariate",
                column=f"{pair.get('col1')},{pair.get('col2')}",
                observed=pair.get("pearson_r"), expected=None, severity="warn",
                description=f"high collinearity: {pair.get('col1')} ~ {pair.get('col2')} (r={pair.get('pearson_r')})",
                evidence_ref=obs_id,
            )
        )
    return findings


def _eval_drift(payload: dict, exp: ExpectationModel, obs_id: str) -> list[Finding]:
    findings: list[Finding] = []
    if payload.get("status") != "computed":
        return findings
    # The drift tool already grades each column (info/warn/critical via PSI). Trust it.
    for col, res in payload.get("per_column", {}).items():
        sev = res.get("severity")
        if sev not in {"warn", "critical"}:
            continue
        findings.append(
            Finding(
                id=_fid(), phase="drift", column=col,
                observed=res.get("psi"), expected=None, severity=sev,
                description=f"distribution drift on '{col}': PSI={res.get('psi')}",
                evidence_ref=obs_id,
            )
        )
    return findings


_DISPATCH = {
    "first_contact": _eval_first_contact,
    "structural": _eval_structural,
    "univariate": _eval_univariate,
    "bivariate": _eval_bivariate,
    "drift": _eval_drift,
}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def evaluate(
    observations: list[Observation],
    expectations: Optional[ExpectationModel],
    state: Optional[dict] = None,
) -> tuple[list[Finding], list[Surprise]]:
    """Diff observations against expectations -> (findings, surprises).

    Pure and deterministic. `state` is accepted for signature symmetry with the
    other nodes but is not required by the rubric.
    """
    exp = expectations or ExpectationModel()
    findings: list[Finding] = []
    for obs in observations:
        rule = _DISPATCH.get(getattr(obs, "tool", None))
        if rule is None:
            continue
        payload = getattr(obs, "payload", {}) or {}
        findings.extend(rule(payload, exp, obs.id))

    # Chase-worthy bar: critical findings, plus warns (which the orchestrator may
    # probe further). Info-level findings are recorded but never spawn a chase.
    surprises = [
        Surprise(
            id=_fid(), finding_id=f.id,
            question=f"Why is this happening? {f.description}",
        )
        for f in findings
        if f.severity in {"critical", "warn"}
    ]
    return findings, surprises


def make_evaluation_partial(
    observations: list[Observation], expectations: Optional[ExpectationModel]
) -> dict[str, Any]:
    """Adapter for the graph: returns the partial-state dict the orchestrator
    merges in. EDAState has no transient `observations` channel, so the
    orchestrator hands observations to this function directly rather than via
    state — keeping secrets/large payloads out of the checkpoint.
    """
    findings, surprises = evaluate(observations, expectations)
    return {"ledger": findings, "open_surprises": surprises}
