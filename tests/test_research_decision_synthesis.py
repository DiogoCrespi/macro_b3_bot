"""Tests for Sprint 4E.3B Research Decision Synthesis & DuckDB Persistence."""

from datetime import datetime, timezone
import json
from pathlib import Path

from macro_b3_bot.infrastructure.store import DatabaseStore
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
            "delta_net_income": 49000000.0,
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
        historical_multiple_position={"observation_count": 9, "median_ev_ebitda": 4.670487},
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
        financial_outcomes=[{"financial_outcome_id": "out_001", "status": "PARTIAL", "delta_net_income": -5000000.0}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "CONFLICTING_MACRO_DIRECTION" in snapshot.critical_blockers


def test_decision_synthesis_blocked_macro_event_not_directional_conflict() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    snapshot = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[
            {"macro_event_id": "evt_001", "factor": "FX_USD_BRL", "status": "BLOCKED"},
        ],
        sector_state={"sector_name": "Retail", "is_active": True, "has_active_signal": True},
        company_contributions=[{"contribution_id": "c_001", "approval_status": "HUMAN_APPROVED"}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "MACRO_EVENT_BLOCKED" in snapshot.critical_blockers
    assert "CONFLICTING_MACRO_DIRECTION" not in snapshot.critical_blockers


def test_decision_synthesis_inactive_sector_with_residual_impact_score() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    # is_active=False even with impact_score != 0 MUST trigger NO_ACTIVE_SECTOR_SIGNAL
    snapshot = synthesizer.synthesize(
        ticker="RAIL3",
        as_of_timestamp=as_of,
        sector_state={
            "sector_name": "Logistics",
            "is_active": False,
            "has_active_signal": False,
            "impact_score": 0.35,
        },
        company_contributions=[{"contribution_id": "c_001", "approval_status": "HUMAN_APPROVED"}],
        financial_outcomes=[{"financial_outcome_id": "out_001", "status": "PARTIAL", "delta_net_income": 1000.0}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "NO_ACTIVE_SECTOR_SIGNAL" in snapshot.critical_blockers


def test_decision_synthesis_partial_outcome_without_numeric_delta() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    # PARTIAL outcome without numeric delta MUST trigger NO_CALCULABLE_FINANCIAL_CHANNEL
    snapshot = synthesizer.synthesize(
        ticker="KLBN11",
        as_of_timestamp=as_of,
        macro_events=[{"macro_event_id": "e1", "factor": "FX", "factor_direction": 1}],
        sector_state={"sector_name": "Paper", "is_active": True, "has_active_signal": True},
        company_contributions=[{"contribution_id": "c1", "approval_status": "HUMAN_APPROVED"}],
        financial_outcomes=[{"financial_outcome_id": "out_001", "status": "PARTIAL", "delta_net_income": None}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "NO_CALCULABLE_FINANCIAL_CHANNEL" in snapshot.critical_blockers


def test_decision_synthesis_pit_lookahead_failure() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    future_time = "2026-06-01T00:00:00Z"
    
    snapshot = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{
            "macro_event_id": "evt_future",
            "factor": "INTEREST_RATES",
            "factor_direction": -1,
            "available_at": future_time,
        }],
        sector_state={"sector_name": "Retail", "is_active": True, "has_active_signal": True},
        company_contributions=[{"contribution_id": "c1", "approval_status": "HUMAN_APPROVED"}],
    )

    assert snapshot.decision == "NO_ACTION"
    assert "LOOKAHEAD_OR_PIT_FAILURE" in snapshot.critical_blockers


def test_decision_snapshot_deterministic_canonical_id() -> None:
    payload1 = {
        "ticker": "MGLU3",
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "decision": "WATCH",
        "confidence": 0.4080,
        "sector_state": {"sector_name": "Retail", "is_active": True},
    }
    payload2 = {
        "ticker": "MGLU3",
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "decision": "WATCH",
        "confidence": 0.4080,
        "sector_state": {"sector_name": "Retail", "is_active": True},
    }
    payload3 = {
        "ticker": "MGLU3",
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "decision": "WATCH",
        "confidence": 0.4080,
        "sector_state": {"sector_name": "Retail_MODIFIED", "is_active": True},
    }

    id1 = ResearchDecisionSnapshot.compute_decision_id(payload1)
    id2 = ResearchDecisionSnapshot.compute_decision_id(payload2)
    id3 = ResearchDecisionSnapshot.compute_decision_id(payload3)

    assert id1 == id2
    assert id1 != id3


def test_duckdb_append_only_idempotent_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "test_store.duckdb"
    store = DatabaseStore(db_path)
    
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    snapshot = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{"macro_event_id": "e1", "factor": "INTEREST_RATES", "factor_direction": -1}],
        sector_state={"sector_name": "Retail", "is_active": True, "has_active_signal": True},
        company_contributions=[{"contribution_id": "c1", "approval_status": "HUMAN_APPROVED"}],
        financial_outcomes=[{"financial_outcome_id": "o1", "status": "PARTIAL", "delta_net_income": 500.0}],
    )

    data_dict = snapshot.model_dump(mode="json")
    
    # Save once
    store.save_research_decision_snapshot(data_dict)
    retrieved = store.get_research_decision_snapshots("MGLU3")
    assert len(retrieved) == 1
    assert retrieved[0]["decision_id"] == snapshot.decision_id

    # Save again (idempotent duplicate save should not crash or create extra row)
    store.save_research_decision_snapshot(data_dict)
    retrieved_after = store.get_research_decision_snapshots("MGLU3")
    assert len(retrieved_after) == 1

    store.close()


def test_real_4e2_audit_multiples_matching() -> None:
    audit_file = Path("data/audits/valuation_4e2_historical_reverse.json")
    if audit_file.exists():
        with open(audit_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        mglu_summary = data.get("summary_by_company", {}).get("MGLU3", {})
        suzb_summary = data.get("summary_by_company", {}).get("SUZB3", {})
        
        mglu_median = mglu_summary.get("percentiles", {}).get("ev_ebitda", {}).get("median")
        suzb_median = suzb_summary.get("percentiles", {}).get("ev_ebitda", {}).get("median")
        
        # Verify exact 4E.2 audit values
        assert mglu_median is not None
        assert abs(mglu_median - 4.670487) < 1e-4
        assert suzb_median is not None
        assert abs(suzb_median - 6.171108) < 1e-4


def test_no_dcf_buy_or_target_price_in_models() -> None:
    snapshot_fields = ResearchDecisionSnapshot.model_fields.keys()
    forbidden_terms = {"dcf", "target_price", "buy_recommendation", "order", "price_target"}
    
    for field in snapshot_fields:
        for forbidden in forbidden_terms:
            assert forbidden not in field.lower(), f"Forbidden term {forbidden} found in snapshot field {field}"
