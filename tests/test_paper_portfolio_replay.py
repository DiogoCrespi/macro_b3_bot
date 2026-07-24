"""Tests for Sprint 4G Paper Portfolio & End-to-End Historical Replay."""

from datetime import datetime, timezone
from pathlib import Path

from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot
from macro_b3_bot.domain.paper_portfolio_models import (
    PaperAllocationEvent,
    PaperPortfolioPolicy,
)
from macro_b3_bot.application.paper_portfolio_engine import PaperPortfolioEngine
from macro_b3_bot.application.historical_replay_engine import HistoricalReplayEngine
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_no_entry_for_no_action_decision() -> None:
    policy = PaperPortfolioPolicy(policy_id="p1")
    engine = PaperPortfolioEngine(policy=policy)
    cutoff = datetime.now(timezone.utc)

    decision = ResearchDecisionSnapshot(
        decision_id="dec_001",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        decision="NO_ACTION",
        critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT"],
        execution_mode="BLOCKED_MISSING_UPSTREAM_INPUT",
    )
    timing = ResearchTimingRiskSnapshot(
        timing_risk_id="t_001",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        research_decision_id="dec_001",
        timing_classification="WAIT_FOR_CONFIRMATION",
        risk_classification="HIGH_RISK",
        execution_mode="BLOCKED_MISSING_UPSTREAM_INPUT",
    )

    evt = engine.evaluate_and_allocate(
        cutoff_dt=cutoff,
        ticker="MGLU3",
        decision_snapshot=decision,
        timing_snapshot=timing,
        execution_session_date="2026-07-25",
        execution_price=10.0,
    )

    assert evt.event_type == "NO_ALLOCATION"
    assert evt.executed_weight == 0.0
    assert engine.positions.get("MGLU3") is None


def test_no_entry_for_wait_for_confirmation_or_avoid_timing() -> None:
    policy = PaperPortfolioPolicy(policy_id="p2")
    engine = PaperPortfolioEngine(policy=policy)
    cutoff = datetime.now(timezone.utc)

    decision = ResearchDecisionSnapshot(
        decision_id="dec_002",
        ticker="SUZB3",
        as_of_timestamp=cutoff.isoformat(),
        decision="WATCH",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    timing = ResearchTimingRiskSnapshot(
        timing_risk_id="t_002",
        ticker="SUZB3",
        as_of_timestamp=cutoff.isoformat(),
        research_decision_id="dec_002",
        timing_classification="WAIT_FOR_CONFIRMATION",
        risk_classification="MODERATE_RISK",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    evt = engine.evaluate_and_allocate(
        cutoff_dt=cutoff,
        ticker="SUZB3",
        decision_snapshot=decision,
        timing_snapshot=timing,
        execution_session_date="2026-07-25",
        execution_price=50.0,
    )

    assert evt.event_type == "NO_ALLOCATION"
    assert "Timing=WAIT_FOR_CONFIRMATION" in evt.reason


def test_valid_watch_and_monitor_triggers_simulated_entry() -> None:
    policy = PaperPortfolioPolicy(policy_id="p3")
    engine = PaperPortfolioEngine(policy=policy)
    cutoff = datetime.now(timezone.utc)

    decision = ResearchDecisionSnapshot(
        decision_id="dec_003",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        decision="WATCH",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
        critical_blockers=[],
    )
    timing = ResearchTimingRiskSnapshot(
        timing_risk_id="t_003",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        research_decision_id="dec_003",
        timing_classification="MONITOR",
        risk_classification="MODERATE_RISK",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    evt = engine.evaluate_and_allocate(
        cutoff_dt=cutoff,
        ticker="MGLU3",
        decision_snapshot=decision,
        timing_snapshot=timing,
        execution_session_date="2026-07-25",
        execution_price=10.0,
    )

    assert evt.event_type == "SIMULATED_ENTRY"
    assert evt.executed_weight > 0.0
    assert engine.positions["MGLU3"].status == "OPEN"


def test_transaction_costs_and_slippage_applied() -> None:
    policy = PaperPortfolioPolicy(policy_id="p4", b3_emoluments_pct=0.00035, base_slippage_pct=0.0010)
    engine = PaperPortfolioEngine(policy=policy)
    cutoff = datetime.now(timezone.utc)

    decision = ResearchDecisionSnapshot(
        decision_id="dec_004",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        decision="WATCH",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    timing = ResearchTimingRiskSnapshot(
        timing_risk_id="t_004",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        research_decision_id="dec_004",
        timing_classification="MONITOR",
        risk_classification="LOW_RISK",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )

    evt = engine.evaluate_and_allocate(
        cutoff_dt=cutoff,
        ticker="MGLU3",
        decision_snapshot=decision,
        timing_snapshot=timing,
        execution_session_date="2026-07-25",
        execution_price=10.0,
    )

    assert evt.transaction_cost > 0.0
    assert evt.slippage_cost > 0.0
    assert engine.total_transaction_costs > 0.0
    assert engine.total_slippage > 0.0


def test_invalidator_triggers_simulated_exit() -> None:
    policy = PaperPortfolioPolicy(policy_id="p5")
    engine = PaperPortfolioEngine(policy=policy)
    cutoff = datetime.now(timezone.utc)

    # 1. Open position
    decision1 = ResearchDecisionSnapshot(
        decision_id="dec_005",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        decision="WATCH",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    timing1 = ResearchTimingRiskSnapshot(
        timing_risk_id="t_005",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        research_decision_id="dec_005",
        timing_classification="MONITOR",
        risk_classification="LOW_RISK",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    engine.evaluate_and_allocate(cutoff_dt=cutoff, ticker="MGLU3", decision_snapshot=decision1, timing_snapshot=timing1, execution_session_date="2026-07-25", execution_price=10.0)
    assert engine.positions["MGLU3"].status == "OPEN"

    # 2. Timing becomes AVOID -> Triggers INVALIDATION_EXIT
    timing2 = ResearchTimingRiskSnapshot(
        timing_risk_id="t_006",
        ticker="MGLU3",
        as_of_timestamp=cutoff.isoformat(),
        research_decision_id="dec_005",
        timing_classification="AVOID",
        risk_classification="HIGH_RISK",
        execution_mode="REAL_UPSTREAM_SYNTHESIS",
    )
    evt_exit = engine.evaluate_and_allocate(cutoff_dt=cutoff, ticker="MGLU3", decision_snapshot=decision1, timing_snapshot=timing2, execution_session_date="2026-07-26", execution_price=11.0)

    assert evt_exit.event_type == "INVALIDATION_EXIT"
    assert engine.positions["MGLU3"].status == "CLOSED"


def test_nav_reconciliation_cash_plus_positions() -> None:
    policy = PaperPortfolioPolicy(policy_id="p6", initial_capital=100000.0)
    engine = PaperPortfolioEngine(policy=policy)
    cutoff = datetime.now(timezone.utc)

    snap = engine.mark_to_market(cutoff, {"MGLU3": 10.0})
    assert abs(snap.nav - (snap.cash_balance + snap.positions_value)) < 0.01
    assert snap.nav == 100000.0


def test_zero_positions_is_valid_replay_result(tmp_path: Path) -> None:
    db_path = tmp_path / "test_replay_zero.duckdb"
    store = DatabaseStore(db_path)

    policy = PaperPortfolioPolicy(policy_id="p_zero", initial_capital=100000.0)
    replay_engine = HistoricalReplayEngine()
    start_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end_dt = datetime(2024, 6, 1, tzinfo=timezone.utc)

    replay_run, steps, report, events, snaps = replay_engine.run_replay(
        store_conn=store.connection,
        policy=policy,
        universe=["MGLU3", "SUZB3"],
        start_date=start_dt,
        end_date=end_dt,
    )

    assert replay_run.status == "COMPLETED"
    assert report.thesis_metrics["simulated_entries"] == 0
    assert snaps[-1].nav == 100000.0
    assert snaps[-1].cash_balance == 100000.0

    store.close()


def test_no_forbidden_terms_in_paper_models() -> None:
    event_fields = PaperAllocationEvent.model_fields.keys()
    forbidden_terms = {"buy", "sell_order", "order_submitted", "order_executed", "dcf", "price_target"}

    for field in event_fields:
        for forbidden in forbidden_terms:
            assert forbidden not in field.lower(), f"Forbidden term {forbidden} found in field {field}"
