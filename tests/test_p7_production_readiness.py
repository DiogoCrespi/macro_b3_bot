import json
from pathlib import Path


def test_p7_audit_is_fail_closed():
    path = Path(__file__).parents[1] / "data" / "audits" / "p7_production_readiness.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "STAGING_READY_ONLY"
    assert payload["checks"]["order_execution_disabled"] is True
    assert payload["checks"]["buy_signals_disabled"] is True
    assert payload["safety"] == {"buy_signals": 0, "orders": 0, "broker_calls": 0, "sandbox_only": True}
    assert "real_broker_integration" in payload["blockers"]
    assert "segregated_human_approval" in payload["blockers"]
