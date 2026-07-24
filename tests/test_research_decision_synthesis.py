"""Tests for Sprint 4E.3 Research Decision Synthesis."""

from datetime import datetime, timezone

from macro_b3_bot.application.research_decision_synthesis import ResearchDecisionSynthesizer
from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot


def test_decision_synthesis_watch_case() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    snapshot = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{
            "macro_event_id": "evt_001",
            "factor": "INTEREST_RATES",
            "factor_direction": -1,
        }],
        sector_state={
            "sector_name": "Retail",
            "is_active": True,
            "has_active_signal": True,
            "impact_score": -0.4,
        },
        company_contributions=[{
            "contribution_id": "c_001",
            "approval_status": "HUMAN_APPROVED",
            "confidence": 0.8,
        }],
        financial_outcomes=[{
            "financial_outcome_id": "out_001",
            "status": "PARTIAL",
        }],
        calibration_results=[{
            "calibration_status": "STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE",
            "validation_gate_passed": False,
        }],
        valuation_assessment={
            "classification": "VALUATION_BLOCKED",
            "fcf_dcf_eligible": False,
            "fcf_status": "NOT_VALUATION_READY",
        },
        historical_multiple_position={"observation_count": 9, "median_ev_ebitda": 11.2},
    )

    assert isinstance(snapshot, ResearchDecisionSnapshot)
    assert snapshot.decision == "WATCH"
    assert snapshot.critical_blockers == []
    assert "FCF_NOT_DCF_READY" in snapshot.noncritical_warnings
    assert "EMPIRICAL_CALIBRATION_INCOMPLETE" in snapshot.noncritical_warnings
    assert "SMALL_VALUATION_SAMPLE" in snapshot.noncritical_warnings
    assert snapshot.confidence_tier in ("LOW", "MEDIUM", "HIGH")
    assert snapshot.decision_id is not None and len(snapshot.decision_id) == 64


def test_decision_synthesis_conflicting_macro_blocker() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    snapshot = synthesizer.synthesize(
        ticker="SUZB3",
        as_of_timestamp=as_of,
        macro_events=[
            {"macro_event_id": "evt_001", "factor": "FX_USD_BRL", "factor_direction": 1},
            {"macro_event_id": "evt_002", "factor": "FX_USD_BRL", "factor_direction": -1},
        ],
        sector_state={"sector_name": "Pulp", "is_active": True, "has_active_signal": True},
        company_contributions=[{"contribution_id": "c_001", "approval_status": "HUMAN_APPROVED"}],
        financial_outcomes=[{"financial_outcome_id": "out_001", "status": "PARTIAL"}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "CONFLICTING_MACRO_DIRECTION" in snapshot.critical_blockers


def test_decision_synthesis_no_active_sector_signal_blocker() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    snapshot = synthesizer.synthesize(
        ticker="RAIL3",
        as_of_timestamp=as_of,
        sector_state={"sector_name": "Logistics", "is_active": False, "has_active_signal": False},
        company_contributions=[{"contribution_id": "c_001", "approval_status": "HUMAN_APPROVED"}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "NO_ACTIVE_SECTOR_SIGNAL" in snapshot.critical_blockers


def test_decision_synthesis_no_approved_exposure_blocker() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    snapshot = synthesizer.synthesize(
        ticker="XYZ3",
        as_of_timestamp=as_of,
        sector_state={"sector_name": "Tech", "is_active": True, "has_active_signal": True},
        company_contributions=[],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "NO_APPROVED_COMPANY_EXPOSURE" in snapshot.critical_blockers


def test_decision_snapshot_deterministic_id() -> None:
    payload1 = {"ticker": "MGLU3", "as_of": "2026-07-24T00:00:00Z", "decision": "WATCH"}
    payload2 = {"ticker": "MGLU3", "as_of": "2026-07-24T00:00:00Z", "decision": "WATCH"}
    payload3 = {"ticker": "MGLU3", "as_of": "2026-07-24T00:00:00Z", "decision": "NO_ACTION"}
    
    id1 = ResearchDecisionSnapshot.compute_decision_id(payload1)
    id2 = ResearchDecisionSnapshot.compute_decision_id(payload2)
    id3 = ResearchDecisionSnapshot.compute_decision_id(payload3)
    
    assert id1 == id2
    assert id1 != id3


def test_no_dcf_buy_or_target_price_in_models() -> None:
    snapshot_fields = ResearchDecisionSnapshot.model_fields.keys()
    forbidden_terms = {"dcf", "target_price", "buy_recommendation", "order", "price_target"}
    
    for field in snapshot_fields:
        for forbidden in forbidden_terms:
            assert forbidden not in field.lower(), f"Forbidden term {forbidden} found in snapshot field {field}"
