"""Offline tests for the EDA Phase 8 synthesizer.

Uses a deterministic mock LLM (no network). Validates:
- All six fixed section headings are present in the report.
- Issue log lists every finding ordered critical→warn→info.
- Every critical finding appears in the report.
- Data dictionary is well-formed (columns + types from expectations).
- report_ref is persisted and retrievable via storage.path_for.
- Propose-only invariant: synthesize() writes exactly one .md file to storage.
- LangGraph node returns {"report": report_ref} partial state update.
"""
from __future__ import annotations

import app.ingestion.storage as _storage_mod
from unittest.mock import patch

import pytest

from app.agents.eda.synthesizer import SECTION_HEADINGS, synthesize, synthesizer
from app.models.eda_schemas import (
    Budget,
    ColumnDtype,
    EDAState,
    ExpectationModel,
    Finding,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class _FakeLLM:
    """Deterministic stub — returns traceable prose, no network call."""

    def invoke(self, prompt: str):  # noqa: ANN001
        class _Resp:
            content = (
                "Stub LLM prose. "
                "Any claim here not traceable to a ledger entry is "
                "[unverified / low-confidence]."
            )
        return _Resp()


def _finding(
    fid: str,
    severity: str,
    column: str | None = None,
    decision: str | None = None,
) -> Finding:
    # Descriptions deliberately omit the ID string so that searching for
    # an ID in the rendered markdown unambiguously finds the issue log row.
    return Finding(
        id=fid,
        phase="test",
        column=column,
        observed="actual_value",
        expected="expected_value",
        severity=severity,  # type: ignore[arg-type]
        description=f"A {severity} issue detected",
        evidence_ref=f"obs_{fid}",
        root_cause=f"Root cause of {severity} condition",
        decision=decision,
    )


def _make_state(ledger: list[Finding]) -> EDAState:
    return EDAState(
        dataset_ref="ref_test_dataset",
        run_id="test_run_001",
        objective="Validate sales transaction data quality",
        grain="one row per transaction",
        provenance="weekly CRM export",
        expectations=ExpectationModel(
            expected_dtypes=[
                ColumnDtype(column="amount", expected_dtype="float64"),
                ColumnDtype(column="status", expected_dtype="string"),
            ],
        ),
        ledger=ledger,
        completed_passes=[],
        open_surprises=[],
        budget=Budget(),
        next_action="synthesize",
        report=None,
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def fixture_ledger() -> list[Finding]:
    """Mixed-severity ledger with ≥2 critical findings (spec requirement)."""
    return [
        _finding("f001", "critical", column="amount",
                 decision="Block pipeline — null amount violates contract"),
        _finding("f002", "warn",     column="status",
                 decision="Flag for manual review"),
        _finding("f003", "info",     column="amount"),
        _finding("f004", "warn",     column=None),
        _finding("f005", "critical", column="status",
                 decision="Reject rows with invalid status codes"),
    ]


@pytest.fixture
def state(fixture_ledger: list[Finding]) -> EDAState:
    return _make_state(fixture_ledger)


@pytest.fixture
def llm() -> _FakeLLM:
    return _FakeLLM()


@pytest.fixture
def result(llm: _FakeLLM, state: EDAState) -> dict[str, str]:
    return synthesize(llm, state)


# --------------------------------------------------------------------------- #
# Section presence
# --------------------------------------------------------------------------- #

class TestAllSectionsPresent:
    def test_all_six_headings_in_report(self, result: dict[str, str]) -> None:
        md = result["markdown"]
        for heading in SECTION_HEADINGS:
            assert heading in md, f"Missing fixed section: {heading!r}"

    def test_report_ref_is_non_empty_string(self, result: dict[str, str]) -> None:
        assert isinstance(result["report_ref"], str)
        assert result["report_ref"], "report_ref must be a non-empty string"


# --------------------------------------------------------------------------- #
# Issue log ordering and completeness
# --------------------------------------------------------------------------- #

class TestIssueLog:
    def _issue_log_slice(self, md: str) -> str:
        """Extract only the Issue Log section to isolate ID positions."""
        h3 = SECTION_HEADINGS[2]   # "## 3. Issue Log"
        h4 = SECTION_HEADINGS[3]   # "## 4. Decisions & Rationale"
        start = md.index(h3)
        end   = md.index(h4)
        return md[start:end]

    def test_all_findings_appear_in_report(
        self, result: dict[str, str], fixture_ledger: list[Finding]
    ) -> None:
        md = result["markdown"]
        for f in fixture_ledger:
            assert f.id in md, f"Finding {f.id!r} missing from report"

    def test_severity_order_critical_warn_info(self, result: dict[str, str]) -> None:
        """Within the issue log section, criticals precede warns which precede infos."""
        section = self._issue_log_slice(result["markdown"])
        # f001=critical, f005=critical, f002=warn, f004=warn, f003=info
        # Sorted order expected: [f001, f005, f002, f004, f003]
        pos = {fid: section.index(fid) for fid in ("f001", "f005", "f002", "f004", "f003")}
        assert pos["f001"] < pos["f002"], "Critical f001 must precede warn f002"
        assert pos["f005"] < pos["f003"], "Critical f005 must precede info f003"
        assert pos["f002"] < pos["f003"], "Warn f002 must precede info f003"
        assert pos["f004"] < pos["f003"], "Warn f004 must precede info f003"

    def test_every_critical_finding_in_report(
        self, result: dict[str, str], fixture_ledger: list[Finding]
    ) -> None:
        md = result["markdown"]
        criticals = [f for f in fixture_ledger if f.severity == "critical"]
        assert len(criticals) >= 1, "Fixture must have at least one critical finding"
        for f in criticals:
            assert f.id in md, f"Critical finding {f.id!r} missing from report"

    def test_issue_log_row_count_matches_ledger(
        self, result: dict[str, str], fixture_ledger: list[Finding]
    ) -> None:
        """Every ledger entry generates exactly one row in the issue log."""
        section = self._issue_log_slice(result["markdown"])
        # Each row starts with "| f"
        row_count = section.count("| f00")
        assert row_count == len(fixture_ledger), (
            f"Expected {len(fixture_ledger)} rows, found {row_count}"
        )


# --------------------------------------------------------------------------- #
# Data dictionary
# --------------------------------------------------------------------------- #

class TestDataDictionary:
    def test_expected_columns_present(self, result: dict[str, str]) -> None:
        md = result["markdown"]
        assert "amount" in md, "Column 'amount' missing from data dictionary"
        assert "status" in md, "Column 'status' missing from data dictionary"

    def test_expected_types_present(self, result: dict[str, str]) -> None:
        md = result["markdown"]
        assert "float64" in md, "Expected dtype 'float64' missing"
        assert "string"  in md, "Expected dtype 'string' missing"

    def test_data_dict_has_table_header(self, result: dict[str, str]) -> None:
        md = result["markdown"]
        assert "| Column |" in md, "Data dictionary table header missing"


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

class TestPersistence:
    def test_report_ref_retrievable_via_storage(self, result: dict[str, str]) -> None:
        path = _storage_mod.path_for(result["report_ref"])
        assert path.exists(), f"Persisted report file not found at: {path}"

    def test_persisted_file_contains_full_report(self, result: dict[str, str]) -> None:
        path = _storage_mod.path_for(result["report_ref"])
        content = path.read_text(encoding="utf-8")
        assert "# EDA Report" in content
        for heading in SECTION_HEADINGS:
            assert heading in content, f"Section {heading!r} missing from file on disk"

    def test_persisted_file_is_utf8_markdown(self, result: dict[str, str]) -> None:
        path = _storage_mod.path_for(result["report_ref"])
        assert path.suffix == ".md", f"Expected .md extension, got: {path.suffix}"


# --------------------------------------------------------------------------- #
# Propose-only invariant (G_synth)
# --------------------------------------------------------------------------- #

class TestProposeOnly:
    def test_report_contains_propose_only_notice(self, result: dict[str, str]) -> None:
        """Report must carry the advisory text marking it as non-executable."""
        md = result["markdown"]
        assert "Propose-only" in md or "propose-only" in md.lower(), (
            "Report missing propose-only advisory notice"
        )

    def test_synthesize_writes_exactly_one_storage_file(
        self, llm: _FakeLLM, state: EDAState
    ) -> None:
        """synthesize() persists only the report file — no additional data writes."""
        with patch.object(
            _storage_mod, "save_upload", wraps=_storage_mod.save_upload
        ) as mock_save:
            synthesize(llm, state)
            assert mock_save.call_count == 1, (
                f"Expected 1 storage write (report only), got {mock_save.call_count}"
            )
            # Must be a .md report file, not a data artifact
            call_args = mock_save.call_args
            filename: str = call_args[0][1] if call_args[0] else call_args[1]["filename"]
            assert filename.endswith(".md"), (
                f"Storage write must produce a .md file, got: {filename!r}"
            )


# --------------------------------------------------------------------------- #
# LangGraph node contract
# --------------------------------------------------------------------------- #

class TestLangGraphNode:
    def test_node_returns_only_report_key(
        self, llm: _FakeLLM, state: EDAState
    ) -> None:
        config = {"configurable": {"llm": llm}}
        update = synthesizer(state, config)
        assert set(update.keys()) == {"report"}, (
            f"Node must return exactly {{'report'}}, got: {set(update.keys())}"
        )

    def test_node_report_ref_retrievable(
        self, llm: _FakeLLM, state: EDAState
    ) -> None:
        config = {"configurable": {"llm": llm}}
        update = synthesizer(state, config)
        path = _storage_mod.path_for(update["report"])
        assert path.exists(), "Node's report_ref does not resolve to a real file"

    def test_node_invokes_llm_from_config(self, state: EDAState) -> None:
        """Node must use config['configurable']['llm'] — not a positional arg."""
        invoked: list[bool] = []

        class _TrackingLLM(_FakeLLM):
            def invoke(self, prompt: str):  # noqa: ANN001
                invoked.append(True)
                return super().invoke(prompt)

        config = {"configurable": {"llm": _TrackingLLM()}}
        synthesizer(state, config)
        assert invoked, "synthesizer node did not invoke the LLM from config"

    def test_node_report_non_empty(self, llm: _FakeLLM, state: EDAState) -> None:
        config = {"configurable": {"llm": llm}}
        update = synthesizer(state, config)
        assert update["report"], "report_ref must be non-empty"

    def test_empty_ledger_still_produces_report(self) -> None:
        """synthesizer must not crash on an empty ledger."""
        empty_state = _make_state([])
        config = {"configurable": {"llm": _FakeLLM()}}
        update = synthesizer(empty_state, config)
        assert update["report"], "report_ref missing for empty ledger"
        path = _storage_mod.path_for(update["report"])
        assert path.exists()


def test_issue_log_escapes_pipe_in_cell_values() -> None:
    """A '|' in a column/description must not break the Markdown table row."""
    f = Finding(
        id="fp01", phase="bivariate", column="col_a,col_b",
        observed="x|y", expected=None, severity="warn",
        description="collinear pair a|b flagged",
        evidence_ref="obs_fp01",
    )
    result = synthesize(_FakeLLM(), _make_state([f]))
    md = result["markdown"]
    assert r"a\|b" in md          # pipe inside description escaped
    assert "collinear pair a|b flagged" not in md  # raw unescaped form absent
