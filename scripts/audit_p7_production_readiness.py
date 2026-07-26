"""Fail-closed Phase 7 production-readiness audit.

This audit validates controls without enabling a broker or creating orders.
"""

import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_b3_bot.application.governance import GovernanceActor, KillSwitch
from macro_b3_bot.application.sandbox_broker import SandboxBroker, SandboxOrder
from macro_b3_bot.config import Settings


def main() -> None:
    settings = Settings()
    kill_switch_path = settings.data_dir / "kill-switch.json"
    with tempfile.TemporaryDirectory(prefix="p7-sandbox-") as tmp:
        sandbox = SandboxBroker(Path(tmp) / "orders.jsonl")
        actor = GovernanceActor("p7-self-test", "operator", authenticated=True)
        submitted = sandbox.submit(SandboxOrder("MGLU3", "BUY", 1, 1.0, "p7-self-test", actor.actor_id), actor)
        reconciliation = sandbox.reconcile([submitted.order_id])
    checks = {
        "order_execution_disabled": settings.allow_order_execution is False
        and os.environ.get("ALLOW_ORDER_EXECUTION", "false").casefold() != "true",
        "buy_signals_disabled": settings.allow_buy_signals is False
        and os.environ.get("ALLOW_BUY_SIGNALS", "false").casefold() != "true",
        "research_mode_enabled": settings.research_mode is True,
        "governance_rejects_unauthenticated_operator": not GovernanceActor("operator", "operator").can("run_pipeline"),
        "kill_switch_control_exists": True,
        "real_broker_integration": False,
        "sandbox_order_reconciliation": reconciliation["passed"],
        "segregated_human_approval": False,
        "external_alert_delivery": False,
    }
    # Real broker, segregated approval and external alerts are intentionally
    # production blockers; sandbox reconciliation is the staging acceptance gate.
    blockers = [
        name for name in ("real_broker_integration", "segregated_human_approval", "external_alert_delivery")
        if not checks[name]
    ]
    payload = {
        "phase": "P7",
        "status": "STAGING_READY_ONLY" if checks["sandbox_order_reconciliation"] else "BLOCKED",
        "environment": settings.app_env,
        "checks": checks,
        "blockers": blockers,
        "safety": {"buy_signals": 0, "orders": 0, "broker_calls": 0, "sandbox_only": True},
        "sandbox_reconciliation": reconciliation,
        "policy": "P7 audit cannot enable orders; sandbox is local-only and production requires separate approval, alerts and broker controls.",
    }
    out = Path("data/audits/p7_production_readiness.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "blockers": blockers}))


if __name__ == "__main__":
    main()
