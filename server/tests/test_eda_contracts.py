"""Smoke tests for the EDA foundation contract (Phase 1).

Guards the two empirically-verified failure modes the contract must not regress:
  1. Models must import + construct on Python 3.9 (PEP 604 union deferral).
  2. Accumulator state fields must APPEND via reducers, not overwrite.
"""
import operator

from app.models.eda_schemas import (
    Budget,
    EDAState,
    ExpectationModel,
    Finding,
    FirstContactObs,
    Surprise,
)
from app.models.eda_events import FindingEvent, serialize


def test_models_construct_on_py39():
    em = ExpectationModel(row_magnitude=1000, notes="ok")
    assert em.row_magnitude == 1000
    f = Finding(
        id="f1", phase="structural", severity="critical",
        description="dtype mismatch", evidence_ref="obs1",
    )
    assert f.evidence_ref == "obs1"
    assert Surprise(id="s1", finding_id="f1", question="why?").chased is False
    assert Budget().max_probes == 20
    assert FirstContactObs(id="obs1").tool == "first_contact"


def test_finding_requires_evidence_ref():
    """Grounding keystone: a finding cannot be built without an evidence_ref."""
    import pytest

    with pytest.raises(Exception):
        Finding(id="f", phase="x", severity="info", description="d")  # no evidence_ref


def test_ledger_reducer_appends():
    """LangGraph reducer semantics: operator.add concatenates, not overwrites."""
    a = [Finding(id="f1", phase="p", severity="info", description="d1", evidence_ref="o1")]
    b = [Finding(id="f2", phase="p", severity="warn", description="d2", evidence_ref="o2")]
    merged = operator.add(a, b)
    assert [f.id for f in merged] == ["f1", "f2"]


def test_event_serialize_has_id_line():
    f = Finding(id="f1", phase="p", severity="info", description="d", evidence_ref="o1")
    frame = serialize(FindingEvent(id=7, finding=f))
    assert frame.startswith("id: 7\n")
    assert frame.endswith("\n\n")


def test_state_has_no_secret_fields():
    assert "api_key" not in EDAState.__annotations__
    assert "llm" not in EDAState.__annotations__
