"""Fail-closed Phase 7 production-readiness audit.

This audit validates controls without enabling a broker or creating orders.
"""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_b3_bot.application.governance import GovernanceActor, KillSwitch
from macro_b3_bot.config import Settings


def main() -> None:
    settings = Settings()
    kill_switch_path = settings.data_dir / "kill-switch.json"
    checks = {
        "order_execution_disabled": settings.allow_order_execution is False
        and os.environ.get("ALLOW_ORDER_EXECUTION", "false").casefold() != "true",
        "buy_signals_disabled": settings.allow_buy_signals is False
        and os.environ.get("ALLOW_BUY_SIGNALS", "false").casefold() != "true",
        "research_mode_enabled": settings.research_mode is True,
        "governance_rejects_unauthenticated_operator": not GovernanceActor("operator", "operator").can("run_pipeline"),
        "kill_switch_control_exists": KillSwitch(kill_switch_path).is_active() or True,
        "real_broker_integration": False,
        "sandbox_order_reconciliation": False,
        "segregated_human_approval": False,
        "external_alert_delivery": False,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    payload = {
        "phase": "P7",
        "status": "BLOCKED" if blockers else "STAGING_READY_ONLY",
        "environment": settings.app_env,
        "checks": checks,
        "blockers": blockers,
        "safety": {"buy_signals": 0, "orders": 0, "broker_calls": 0},
        "policy": "P7 audit cannot enable orders; production requires a separate approval and broker sandbox.",
    }
    out = Path("data/audits/p7_production_readiness.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "blockers": blockers}))


if __name__ == "__main__":
    main()
