from pathlib import Path

import pytest

from macro_b3_bot.application.governance import GovernanceActor, KillSwitch
from macro_b3_bot.application.sandbox_broker import SandboxBroker, SandboxOrder


def _order() -> SandboxOrder:
    return SandboxOrder("MGLU3", "BUY", 10, 3.25, "decision-p7", "operator-1")


def test_unauthenticated_operator_is_rejected(tmp_path: Path) -> None:
    broker = SandboxBroker(tmp_path / "orders.jsonl")
    with pytest.raises(PermissionError):
        broker.submit(_order(), GovernanceActor("operator-1", "operator", authenticated=False))


def test_authenticated_operator_is_sandbox_only_and_idempotent(tmp_path: Path) -> None:
    broker = SandboxBroker(tmp_path / "orders.jsonl")
    actor = GovernanceActor("operator-1", "operator", authenticated=True)
    first = broker.submit(_order(), actor)
    second = broker.submit(_order(), actor)
    assert first.order_id == second.order_id
    assert first.status == "SANDBOX_ACCEPTED"
    assert broker.reconcile([first.order_id]) == {
        "matched": [first.order_id], "missing": [], "extra": [], "passed": True
    }


def test_kill_switch_blocks_sandbox_submission(tmp_path: Path) -> None:
    path = tmp_path / "kill-switch.json"
    kill_switch = KillSwitch(path)
    kill_switch.activate(GovernanceActor("admin", "administrator", authenticated=True), "test")
    broker = SandboxBroker(tmp_path / "orders.jsonl", kill_switch)
    with pytest.raises(RuntimeError, match="kill switch"):
        broker.submit(_order(), GovernanceActor("operator-1", "operator", authenticated=True))


def test_reconciliation_reports_missing_and_extra(tmp_path: Path) -> None:
    broker = SandboxBroker(tmp_path / "orders.jsonl")
    actor = GovernanceActor("operator-1", "operator", authenticated=True)
    order = broker.submit(_order(), actor)
    result = broker.reconcile(["missing", order.order_id])
    assert result["missing"] == ["missing"]
    assert result["passed"] is False
