"""Persist issuer-disclosed adjusted FCF evidence for the P5 pilot.

Only values explicitly disclosed by the issuer are accepted.  This script does
not infer maintenance capex from total capex and leaves MGLU3 blocked because
its public material does not provide that split in the selected PIT package.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_b3_bot.domain.financial_bridge_models import (
    CashFlowNormalizationAdjustment,
    NormalizedCashFlowSnapshot,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 7, 26, 23, 59, 59, tzinfo=timezone.utc)
RUN_ID = "issuer_adjusted_fcf_20260726"


EVIDENCE = {
    "SUZB3": {
        "document_id": "SUZANO_4Q25_EARNINGS_RELEASE",
        "document_url": "https://www.sec.gov/Archives/edgar/data/909327/000090932726000025/earningsrelease_4t25xen.htm",
        "document_checksum": "C95B51BC5BADC605A9B64FAF714A203024DCB7BB0EC75FAFE7E314F8FAAE77BB",
        "available_at": "2026-02-11T00:00:00+00:00",
        "source_location": "SEC_HTML:lines_313-323,337-339",
        "adjusted_operating_cash_flow": 13_856_000_000.0,
        "maintenance_capex": 7_880_000_000.0,
        "normalized_fcf": 10_647_000_000.0,
        "formula": "issuer_reported_free_cash_flow_adjusted_2025",
        "components": {
            "adjusted_ebitda": 21_736_000_000.0,
            "total_capex": -12_584_000_000.0,
            "leases_ifrs16": -1_448_000_000.0,
            "working_capital_change": 1_572_000_000.0,
            "net_interest": -4_714_000_000.0,
            "income_taxes": -290_000_000.0,
            "capex_ex_maintenance": 5_844_000_000.0,
            "dividends_added_back": 2_400_000_000.0,
            "non_recurring_items_excluded_from_adjusted_ebitda": True,
        },
    },
    "KLBN11": {
        "document_id": "ITR_12653_2026-03-31_v1",
        "document_url": "https://api.mziq.com/mzfilemanager/v2/d/1c41fa99-efe7-4e72-81dd-5b571f5aa376/301af57c-2352-5293-7ddb-044da693a4c5?origin=2",
        "document_checksum": "144E384BC541A14088E47F3026CF62EF9137895686472A8F5B8B47B746BA3717",
        "available_at": "2026-07-19T10:39:30+00:00",
        "source_location": "ITR_P46/P45:ROIC_LTM_and_FCF_adjusted",
        "adjusted_operating_cash_flow": 4_224_000_000.0,
        "maintenance_capex": 3_197_000_000.0,
        "normalized_fcf": 1_152_000_000.0,
        "formula": "issuer_reported_free_cash_flow_adjusted_ltm_1Q26",
        "components": {
            "adjusted_ebitda_ltm": 7_658_000_000.0,
            "capex_ltm": -3_066_000_000.0,
            "leases_ifrs16_ltm": -327_000_000.0,
            "interest_paid_received_ltm": -1_955_000_000.0,
            "income_taxes_ltm": -237_000_000.0,
            "working_capital_change_ltm": -1_050_000_000.0,
            "dividends_jcp_ltm": -1_181_000_000.0,
            "special_and_expansion_projects_excluded": 788_000_000.0,
            "dividends_added_back": 1_181_000_000.0,
            "non_recurring_projects_excluded": True,
        },
    },
}


def _snapshot(ticker: str, item: dict[str, object], baseline: dict[str, object]) -> NormalizedCashFlowSnapshot:
    doc = str(item["document_id"])
    adjustment_ids = [
        hashlib.sha256(f"{RUN_ID}|{ticker}|maintenance_capex".encode()).hexdigest()[:24],
        hashlib.sha256(f"{RUN_ID}|{ticker}|non_recurring".encode()).hexdigest()[:24],
    ]
    period_end = baseline["latest_quarter"]
    adjustments = [
        CashFlowNormalizationAdjustment(
            adjustment_id=adjustment_ids[0],
            field_name="maintenance_capex",
            value=float(item["maintenance_capex"]),
            sign=-1,
            period_end=period_end,
            source_ids=[doc, str(item["document_checksum"])],
            rationale="Issuer explicitly labels the amount as maintenance capex.",
            recurrence="RECURRING",
            confidence=0.96,
            formula="issuer_disclosed_maintenance_capex",
        ),
        CashFlowNormalizationAdjustment(
            adjustment_id=adjustment_ids[1],
            field_name="non_recurring_items_excluded",
            value=0.0,
            sign=1,
            period_end=period_end,
            source_ids=[doc, str(item["document_checksum"])],
            rationale="Issuer adjusted cash-flow/EBITDA presentation explicitly excludes non-recurring or discretionary items listed in the bridge.",
            recurrence="NON_RECURRING",
            confidence=0.90,
            formula="issuer_disclosed_adjusted_fcf_bridge",
        ),
    ]
    snapshot_id = hashlib.sha256(
        f"{RUN_ID}|{ticker}|{item['document_checksum']}|{item['normalized_fcf']}".encode()
    ).hexdigest()[:24]
    return NormalizedCashFlowSnapshot(
        snapshot_id=snapshot_id,
        ticker=ticker,
        as_of_timestamp=AS_OF,
        reported_operating_cash_flow=float(baseline["ttm_operating_cash_flow"]),
        reported_capex=float(baseline["ttm_capex"]),
        levered_fcf_proxy=float(baseline["ttm_fcf"]),
        normalized_operating_cash_flow=float(item["adjusted_operating_cash_flow"]),
        maintenance_capex=float(item["maintenance_capex"]),
        normalized_levered_fcf=float(item["normalized_fcf"]),
        statistical_normalized_fcf_proxy=float(item["normalized_fcf"]),
        normalization_type="ISSUER_DISCLOSED_ADJUSTED_FCF",
        normalization_status="VALUATION_READY",
        dcf_eligible=True,
        adjustments=adjustments,
        methodology_version="P5-issuer-disclosed-adjusted-fcf-v1",
        confidence=0.90,
        run_id=RUN_ID,
        normalization_formula=str(item["formula"]),
        normalization_components=item["components"],
        source_document_ids=[doc],
    )


def main() -> None:
    store = DatabaseStore(ROOT / "data/audit.duckdb")
    outputs = []
    for ticker, item in EVIDENCE.items():
        row = store.connection.execute(
            """
            SELECT latest_quarter,baseline_payload
            FROM financial_baseline_snapshots
            WHERE ticker=? AND as_of_timestamp<=?
            ORDER BY as_of_timestamp DESC, created_at DESC LIMIT 1
            """,
            [ticker, AS_OF.replace(tzinfo=None)],
        ).fetchone()
        if row is None:
            raise RuntimeError(f"missing PIT baseline for {ticker}")
        baseline_payload = json.loads(row[1])
        baseline = {
            "latest_quarter": row[0],
            "ttm_operating_cash_flow": baseline_payload["ttm_operating_cash_flow"],
            "ttm_capex": baseline_payload["ttm_capex"],
            "ttm_fcf": baseline_payload["ttm_fcf"],
        }
        snapshot = _snapshot(ticker, item, baseline)
        store.save_normalized_cash_flow_snapshot(snapshot.model_dump(mode="json"))
        outputs.append({
            "ticker": ticker,
            "snapshot": snapshot.model_dump(mode="json"),
            "source_url": item["document_url"],
            "document_checksum": item["document_checksum"],
        })
    store.connection.commit()
    store.close()
    output = {
        "run_id": RUN_ID,
        "as_of_timestamp": AS_OF.isoformat(),
        "outputs": outputs,
        "blocked": {
            "MGLU3": [
                "ISSUER_MAINTENANCE_CAPEX_SPLIT_NOT_FOUND",
                "NON_RECURRING_CFO_RECONCILIATION_NOT_FOUND",
            ]
        },
        "valuation": "DCF remains gated by calibration/market gates",
    }
    path = ROOT / "data/audits/issuer_adjusted_fcf_20260726.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
