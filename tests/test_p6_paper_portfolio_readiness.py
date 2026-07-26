"""Acceptance checks for the persisted Phase 6 paper replay."""

import json
from pathlib import Path


def _artifact() -> dict:
    path = Path(__file__).parents[1] / "data" / "audits" / "p6_paper_portfolio_readiness.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_p6_replay_is_persisted_in_canonical_store():
    artifact = _artifact()
    assert artifact["database"].endswith("audit.duckdb")
    assert artifact["reconciliation"]["run_persisted"] is True
    assert artifact["reconciliation"]["snapshots_persisted"] is True
    assert artifact["reconciliation"]["performance_persisted"] is True
    assert artifact["sessions"] >= 30
    assert artifact["persisted_rows"]["portfolio_snapshots"] >= artifact["decision_cutoffs"]


def test_p6_no_action_replay_is_fail_closed():
    artifact = _artifact()
    assert artifact["status"] == "PAPER_REPLAY_NO_ACTION"
    assert artifact["risk_controls"]["buy_signals"] == 0
    assert artifact["risk_controls"]["order_executions"] == 0
    assert artifact["risk_controls"]["real_broker_integrations"] == 0
    assert artifact["reconciliation"]["final_nav_equals_initial_when_no_allocations"] is True


def test_p6_does_not_claim_performance_without_allocations():
    artifact = _artifact()
    assert artifact["allocation_events_generated"] >= artifact["evaluations"]
    assert "NO_APPROVED_DECISIONS_FOR_ALLOCATION" in artifact["blockers"]


def test_p6_allocation_eligibility_does_not_fabricate_approvals():
    path = Path(__file__).parents[1] / "data" / "audits" / "p6_allocation_eligibility.json"
    eligibility = json.loads(path.read_text(encoding="utf-8"))
    assert eligibility["status"] == "NO_APPROVED_DECISIONS"
    assert eligibility["total_decisions"] == 5
    assert eligibility["eligible_for_paper_allocation"] == 0
    assert eligibility["safety"] == {
        "approvals_created": 0,
        "buy_signals_created": 0,
        "orders_created": 0,
    }
