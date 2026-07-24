"""Tests for Sprint 4E.3D Upstream Source Contract Closure & Decision Synthesis."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys

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
            "available_at": "2025-12-01T18:00:00Z",
        }],
        sector_state={
            "sector_name": "Retail",
            "is_active": True,
            "has_active_signal": True,
            "impact_score": -0.4,
            "available_at": "2025-12-01T18:00:00Z",
        },
        company_contributions=[{
            "contribution_id": "c_001",
            "channel": "floating_rate_debt",
            "approval_status": "HUMAN_APPROVED",
            "confidence": 0.8,
            "available_at": "2025-12-01T18:00:00Z",
        }],
        financial_outcomes=[{
            "financial_outcome_id": "out_001",
            "contribution_id": "c_001",
            "baseline_id": "base_001",
            "status": "PARTIAL",
            "delta_net_income": 49000000.0,
            "available_at": "2026-03-01T18:00:00Z",
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
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
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
            {"macro_event_id": "evt_001", "factor": "FX_USD_BRL", "factor_direction": 1, "available_at": "2026-01-01T00:00:00Z"},
            {"macro_event_id": "evt_002", "factor": "FX_USD_BRL", "factor_direction": -1, "available_at": "2026-01-01T00:00:00Z"},
        ],
        sector_state={"sector_name": "Pulp", "is_active": True, "has_active_signal": True, "available_at": "2026-01-01T00:00:00Z"},
        company_contributions=[{"contribution_id": "c_001", "channel": "export_revenue", "approval_status": "HUMAN_APPROVED", "available_at": "2026-01-01T00:00:00Z"}],
        financial_outcomes=[{"financial_outcome_id": "out_001", "status": "PARTIAL", "delta_net_income": -5000000.0, "available_at": "2026-01-01T00:00:00Z"}],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    assert snapshot.decision == "NO_ACTION"
    assert "CONFLICTING_MACRO_DIRECTION" in snapshot.critical_blockers


def test_decision_synthesis_blocked_mode_forces_no_action() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)

    # Even with all inputs present, BLOCKED_MISSING_UPSTREAM_INPUT mode MUST strictly force NO_ACTION
    snapshot = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{"macro_event_id": "e1", "factor": "RATES", "factor_direction": -1, "available_at": "2026-01-01T00:00:00Z"}],
        sector_state={"sector_name": "Retail", "is_active": True, "has_active_signal": True, "available_at": "2026-01-01T00:00:00Z"},
        company_contributions=[{"contribution_id": "c1", "channel": "rates", "approval_status": "HUMAN_APPROVED", "available_at": "2026-01-01T00:00:00Z"}],
        financial_outcomes=[{"financial_outcome_id": "out1", "status": "PARTIAL", "delta_net_income": 49000000.0, "available_at": "2026-01-01T00:00:00Z"}],
        execution_mode="BLOCKED_MISSING_UPSTREAM_INPUT",
    )

    assert snapshot.decision == "NO_ACTION"
    assert "BLOCKED_MISSING_UPSTREAM_INPUT" in snapshot.critical_blockers


def test_decision_synthesis_non_finite_financial_values_blocked() -> None:
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)

    snapshot_nan = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{"macro_event_id": "e1", "factor": "RATES", "factor_direction": -1, "available_at": "2026-01-01T00:00:00Z"}],
        sector_state={"sector_name": "Retail", "is_active": True, "has_active_signal": True, "available_at": "2026-01-01T00:00:00Z"},
        company_contributions=[{"contribution_id": "c1", "channel": "rates", "approval_status": "HUMAN_APPROVED", "available_at": "2026-01-01T00:00:00Z"}],
        financial_outcomes=[{"financial_outcome_id": "out1", "status": "PARTIAL", "delta_net_income": math.nan, "available_at": "2026-01-01T00:00:00Z"}],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    assert snapshot_nan.decision == "NO_ACTION"
    assert "NO_CALCULABLE_FINANCIAL_CHANNEL" in snapshot_nan.critical_blockers


def test_static_no_loader_fallback_defaults() -> None:
    runner_file = Path("scripts/run_sprint4e3_decision_synthesis.py")
    content = runner_file.read_text(encoding="utf-8")
    
    # Assert zero hard-coded ticker fallback branches or literal fallback defaults
    assert "elif ticker ==" not in content
    assert "evt_selic_cut_2025_001" not in content
    assert "sec_comercio_varejo_2025_q4" not in content
    assert "contrib_mglu_rates_001" not in content
    assert 'or "sec_snapshot_' not in content
    assert 'or "contrib_real_' not in content
    assert 'or "base_real_' not in content


def test_runner_requires_as_of_argument() -> None:
    res = subprocess.run([sys.executable, "scripts/run_sprint4e3_decision_synthesis.py"], capture_output=True, text=True)
    assert res.returncode != 0
    assert "Usage error: --as-of <ISO_TIMESTAMP> is required." in res.stdout or "Usage error" in res.stderr


def test_generated_decisions_json_has_no_null_contribution_ids() -> None:
    audit_file = Path("data/audits/research_4e3_decisions.json")
    if audit_file.exists():
        with open(audit_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        for dec in data.get("decisions", []):
            for contrib in dec.get("company_contributions", []):
                assert contrib.get("contribution_id") is not None, f"Null contribution_id found in {dec.get('ticker')}"
                assert contrib.get("channel") is not None, f"Null channel found in {dec.get('ticker')}"


def test_duckdb_append_only_idempotent_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "test_store.duckdb"
    store = DatabaseStore(db_path)
    
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    snapshot = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{"macro_event_id": "e1", "factor": "INTEREST_RATES", "factor_direction": -1, "available_at": "2026-01-01T00:00:00Z"}],
        sector_state={"sector_name": "Retail", "is_active": True, "has_active_signal": True, "available_at": "2026-01-01T00:00:00Z"},
        company_contributions=[{"contribution_id": "c1", "channel": "debt", "approval_status": "HUMAN_APPROVED", "available_at": "2026-01-01T00:00:00Z"}],
        financial_outcomes=[{"financial_outcome_id": "o1", "status": "PARTIAL", "delta_net_income": 500.0, "available_at": "2026-01-01T00:00:00Z"}],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    data_dict = snapshot.model_dump(mode="json")
    
    # Save once
    store.save_research_decision_snapshot(data_dict)
    retrieved = store.get_research_decision_snapshots("MGLU3")
    assert len(retrieved) == 1
    assert retrieved[0]["decision_id"] == snapshot.decision_id
    assert retrieved[0]["execution_mode"] == "REAL_UPSTREAM_SYNTHESIS"

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
