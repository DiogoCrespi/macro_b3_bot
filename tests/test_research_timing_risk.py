"""Tests for Sprint 4F.2 Research Timing, Risk & Invalidation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot
from macro_b3_bot.application.research_timing_risk_synthesis import ResearchTimingRiskSynthesizer
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_no_default_market_metrics_when_data_missing() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_mglu_001",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(
        decision_snapshot=decision_snapshot,
        as_of_timestamp=as_of,
        market_quotes=[],  # No market quotes provided
    )

    assert snapshot.market_metrics["status"] == "UNKNOWN_INSUFFICIENT_MARKET_DATA"
    assert snapshot.volatility_state["volatility_status"] == "UNKNOWN_INSUFFICIENT_MARKET_DATA"
    assert snapshot.volatility_state["historical_volatility"] is None
    assert snapshot.liquidity_state["liquidity_status"] == "UNKNOWN_INSUFFICIENT_MARKET_DATA"
    assert snapshot.liquidity_state["daily_volume_brl"] is None


def test_monotonic_risk_severity_aggregation() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    # 1. Base missing upstream snapshot (HIGH_RISK = 4)
    base_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_mglu_base",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    s_base = synthesizer.synthesize(decision_snapshot=base_snapshot, as_of_timestamp=as_of)
    assert s_base.risk_severity_level == 4
    assert s_base.risk_classification == "HIGH_RISK"

    # 2. Snapshot with missing upstream AND conflicting macro direction (Must remain HIGH_RISK = 4, never drop to ELEVATED_RISK = 3!)
    combined_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_suzb_combined",
        ticker="SUZB3",
        as_of_timestamp=as_of.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT", "CONFLICTING_MACRO_DIRECTION"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    s_combined = synthesizer.synthesize(decision_snapshot=combined_snapshot, as_of_timestamp=as_of)
    assert s_combined.risk_severity_level >= 4
    assert s_combined.risk_classification in ("HIGH_RISK", "UNACCEPTABLE_RISK")


def test_timing_semantics_missing_upstream_wait_for_confirmation() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_mglu_missing",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(decision_snapshot=decision_snapshot, as_of_timestamp=as_of)
    assert snapshot.timing_classification == "WAIT_FOR_CONFIRMATION"


def test_timing_semantics_material_conflict_avoid() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_suzb_conflict",
        ticker="SUZB3",
        as_of_timestamp=as_of.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["CONFLICTING_MACRO_DIRECTION"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(decision_snapshot=decision_snapshot, as_of_timestamp=as_of)
    assert snapshot.timing_classification == "AVOID"


def test_event_freshness_half_life_decay() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)
    event_available = as_of - timedelta(days=90)  # Exactly 1 half-life ago (90 days)

    events = [
        {
            "macro_event_id": "evt_selic_cut_90d",
            "available_at": event_available.isoformat(),
            "event_status": "ACTIVE_MONITORED",
            "importance": "HIGH",
        }
    ]

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_freshness_001",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="WATCH",
        macro_event_ids=["evt_selic_cut_90d"],
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    snapshot = synthesizer.synthesize(
        decision_snapshot=decision_snapshot,
        as_of_timestamp=as_of,
        macro_events=events,
    )

    assert len(snapshot.catalysts) == 1
    c = snapshot.catalysts[0]
    assert c["age_days"] == 90
    assert abs(c["decay_factor"] - 0.50) < 0.05  # 2^(-90/90) = 0.50


def test_pit_decision_selection_rejects_future_decisions(tmp_path: Path) -> None:
    db_path = tmp_path / "test_pit_selection.duckdb"
    store = DatabaseStore(db_path)

    as_of_past = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    as_of_future = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    # 1. Save future decision (2026-06-01)
    future_decision = {
        "decision_id": "dec_future_001",
        "ticker": "MGLU3",
        "as_of_timestamp": as_of_future.isoformat(),
        "decision": "WATCH",
        "confidence": 0.80,
        "confidence_tier": "HIGH",
        "canonical_payload_json": "{}",
        "input_ids": {},
        "methodology_version": "4E.3-research-decision-synthesis-v1",
        "execution_mode": "REAL_UPSTREAM_SYNTHESIS",
        "critical_blockers": [],
        "invalidation_conditions": [],
        "noncritical_warnings": [],
        "macro_event_ids": [],
    }
    store.save_research_decision_snapshot(future_decision)

    # 2. Query PIT decision for cutoff 2026-01-01 -> Must return None!
    pit_dec = store.get_latest_research_decision_snapshot_pit("MGLU3", as_of_past)
    assert pit_dec is None

    # 3. Query PIT decision for cutoff 2026-06-01 -> Must return future_decision!
    pit_dec_future = store.get_latest_research_decision_snapshot_pit("MGLU3", as_of_future)
    assert pit_dec_future is not None
    assert pit_dec_future["decision_id"] == "dec_future_001"

    store.close()


def test_content_hash_changes_with_market_quotes_window() -> None:
    synthesizer = ResearchTimingRiskSynthesizer()
    as_of = datetime.now(timezone.utc)

    decision_snapshot = ResearchDecisionSnapshot(
        decision_id="dec_hash_001",
        ticker="MGLU3",
        as_of_timestamp=as_of.isoformat(),
        decision="WATCH",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    quotes1 = [{"trade_date": f"2026-01-{i:02d}", "close_price": 10.0 + i} for i in range(1, 25)]
    quotes2 = [{"trade_date": f"2026-01-{i:02d}", "close_price": 20.0 + i} for i in range(1, 25)]

    s1 = synthesizer.synthesize(decision_snapshot=decision_snapshot, as_of_timestamp=as_of, market_quotes=quotes1)
    s2 = synthesizer.synthesize(decision_snapshot=decision_snapshot, as_of_timestamp=as_of, market_quotes=quotes2)

    assert s1.timing_risk_id != s2.timing_risk_id


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
