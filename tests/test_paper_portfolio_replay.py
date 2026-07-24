"""Tests for Sprint 4G.2 Paper Portfolio & End-to-End Historical Replay."""

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot
from macro_b3_bot.domain.paper_portfolio_models import (
    PaperAllocationEvent,
    PaperPortfolioPolicy,
)
from macro_b3_bot.application.paper_portfolio_engine import PaperPortfolioEngine
from macro_b3_bot.application.historical_replay_engine import HistoricalReplayEngine
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_no_synthetic_decision_or_timing_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "test_no_synth.duckdb"
    store = DatabaseStore(db_path)

    policy = PaperPortfolioPolicy(policy_id="p_no_synth", initial_capital=100000.0)
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
    for step in steps:
        assert step.is_blocked_step is True
        for dec_id in step.decision_ids.values():
            assert "blocked_no_decision" not in dec_id
        for t_id in step.timing_risk_ids.values():
            assert "timing_blocked" not in t_id

    store.close()


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


def test_open_price_execution_and_missing_price_block() -> None:
    policy = PaperPortfolioPolicy(policy_id="p_price")
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

    # Missing price (None) MUST block execution!
    evt_blocked = engine.evaluate_and_allocate(
        cutoff_dt=cutoff,
        ticker="MGLU3",
        decision_snapshot=decision,
        timing_snapshot=timing,
        execution_session_date="2026-07-25",
        execution_price=None,
    )

    assert evt_blocked.event_type == "NO_ALLOCATION"
    assert evt_blocked.reason == "PAPER_ENTRY_EXECUTION_BLOCKED_MISSING_PRICE"


def test_mandatory_cli_arguments() -> None:
    res = subprocess.run([sys.executable, "scripts/run_sprint4g_paper_portfolio_replay.py"], capture_output=True, text=True)
    assert res.returncode != 0
    assert "Usage error: Mandatory arguments missing" in res.stdout or "Usage error" in res.stderr


def test_no_forbidden_terms_in_paper_models() -> None:
    event_fields = PaperAllocationEvent.model_fields.keys()
    forbidden_terms = {"buy", "sell_order", "order_submitted", "order_executed", "dcf", "price_target"}

    for field in event_fields:
        for forbidden in forbidden_terms:
            assert forbidden not in field.lower(), f"Forbidden term {forbidden} found in field {field}"
