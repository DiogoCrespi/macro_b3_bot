import json
from pathlib import Path


def test_issuer_adjusted_fcf_evidence_is_persisted_and_scoped() -> None:
    root = Path(__file__).parents[1]
    artifact = json.loads(
        (root / "data" / "audits" / "issuer_adjusted_fcf_20260726.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["ticker"] for item in artifact["outputs"]} == {"SUZB3", "KLBN11"}
    for item in artifact["outputs"]:
        snapshot = item["snapshot"]
        assert snapshot["normalization_type"] == "ISSUER_DISCLOSED_ADJUSTED_FCF"
        assert snapshot["normalization_status"] == "VALUATION_READY"
        assert snapshot["dcf_eligible"] is True
        assert snapshot["source_document_ids"]
        assert snapshot["normalization_formula"].startswith("issuer_reported_")
        assert all(adjustment["source_ids"] for adjustment in snapshot["adjustments"])
    assert "MGLU3" in artifact["blocked"]
    assert "ISSUER_MAINTENANCE_CAPEX_SPLIT_NOT_FOUND" in artifact["blocked"]["MGLU3"]

