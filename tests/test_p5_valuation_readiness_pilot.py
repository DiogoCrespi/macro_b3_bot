import json
from pathlib import Path


def test_p5_readiness_artifact_is_fail_closed_and_pit() -> None:
    path = Path(__file__).parents[1] / "data" / "audits" / "valuation_p5_readiness.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["sprint"] == "P5"
    assert payload["acceptance_checks"] == {
        "formal_gate_present": True,
        "fcf_proxy_blocked": True,
        "no_fair_value_or_price_target": True,
        "pit_cutoff": True,
    }
    assert payload["safety"] == {
        "dcf_executed": False,
        "fair_values": False,
        "price_targets": False,
        "buy_signals": False,
        "orders": False,
    }
    assert len(payload["results"]) == 5
    assert all(item["valuation_eligible"] is False for item in payload["results"])
    assert all(item["dcf_eligible"] is False for item in payload["results"])
    assert all(item["fair_value"] is None for item in payload["results"])
    assert all(item["price_target"] is None for item in payload["results"])

