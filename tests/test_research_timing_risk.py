"""Tests for Sprint 4F Research Timing, Risk & Invalidation."""

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot
from macro_b3_bot.application.research_timing_risk_synthesis import ResearchTimingRiskSynthesizer
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_timing_risk_synthesis_watch_case() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_mglu_001",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="WATCH",
        macro_event_ids=["evt_selic_cut_001"],
        confidence=0.85,
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(
        decision_snapshot=decision_snapshot,
        as_of_timestamp=as_of,
    )

    assert isinstance(snapshot, ResearchTimingRiskSnapshot)
    assert snapshot.timing_classification == "MONITOR"
    assert snapshot.risk_classification == "LOW_RISK"
    assert snapshot.timing_risk_id is not None and len(snapshot.timing_risk_id) == 64


def test_timing_risk_synthesis_no_action_case() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_suzb_001",
        ticker="SUZB3",
        as_of_timestamp=as_of.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["CONFLICTING_MACRO_DIRECTION"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(
        decision_snapshot=decision_snapshot,
        as_of_timestamp=as_of,
    )

    assert snapshot.timing_classification == "AVOID"
    assert snapshot.risk_classification == "ELEVATED_RISK"
    assert any("CONFLICTING_MACRO_DIRECTION" in inv for inv in snapshot.thesis_invalidators)


def test_timing_risk_deterministic_hash() -> None:
    payload1 = {
        "ticker": "MGLU3",
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "research_decision_id": "dec_001",
        "timing_classification": "MONITOR",
        "risk_classification": "LOW_RISK",
    }
    payload2 = {
        "ticker": "MGLU3",
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "research_decision_id": "dec_001",
        "timing_classification": "MONITOR",
        "risk_classification": "LOW_RISK",
    }
    payload3 = {
        "ticker": "MGLU3",
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "research_decision_id": "dec_001",
        "timing_classification": "AVOID",
        "risk_classification": "HIGH_RISK",
    }

    id1 = ResearchTimingRiskSnapshot.compute_timing_risk_id(payload1)
    id2 = ResearchTimingRiskSnapshot.compute_timing_risk_id(payload2)
    id3 = ResearchTimingRiskSnapshot.compute_timing_risk_id(payload3)

    assert id1 == id2
    assert id1 != id3


def test_duckdb_timing_risk_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "test_timing_store.duckdb"
    store = DatabaseStore(db_path)

    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)
    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_test_001",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="WATCH",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(
        decision_snapshot=decision_snapshot,
        as_of_timestamp=as_of,
    )

    data_dict = snapshot.model_dump(mode="json")
    
    # Save once
    store.save_research_timing_risk_snapshot(data_dict)
    retrieved = store.get_research_timing_risk_snapshots("MGLU3")
    assert len(retrieved) == 1
    assert retrieved[0]["timing_risk_id"] == snapshot.timing_risk_id

    # Idempotent save
    store.save_research_timing_risk_snapshot(data_dict)
    retrieved_after = store.get_research_timing_risk_snapshots("MGLU3")
    assert len(retrieved_after) == 1

    store.close()


def test_runner_sprint4f_requires_as_of_argument() -> None:
    res = subprocess.run([sys.executable, "scripts/run_sprint4f_timing_risk.py"], capture_output=True, text=True)
    assert res.returncode != 0
    assert "Usage error: --as-of <ISO_TIMESTAMP> is required." in res.stdout or "Usage error" in res.stderr


def test_no_forbidden_terms_in_timing_models() -> None:
    snapshot_fields = ResearchTimingRiskSnapshot.model_fields.keys()
    forbidden_terms = {"dcf", "target_price", "buy_recommendation", "order", "price_target"}

    for field in snapshot_fields:
        for forbidden in forbidden_terms:
            assert forbidden not in field.lower(), f"Forbidden term {forbidden} found in timing snapshot field {field}"
