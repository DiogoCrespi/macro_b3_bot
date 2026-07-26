"""Sprint P5 valuation-readiness gate pilot; never emits fair value or price targets."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_b3_bot.application.valuation_readiness import ValuationReadinessGate
from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    FinancialBaselineSnapshot,
    MarketSnapshotPIT,
    NormalizedCashFlowSnapshot,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


def _baseline(row) -> FinancialBaselineSnapshot:
    payload = json.loads(row[5])
    return FinancialBaselineSnapshot(
        baseline_id=row[0], ticker=row[1], cvm_code=row[2],
        as_of_timestamp=row[3], latest_quarter=row[4], **payload,
        field_evidence=json.loads(row[6]), missing_fields=json.loads(row[7]),
        confidence=row[8], methodology_version=row[9], run_id=row[10], created_at=row[11],
    )


def _is_pit_timestamp(value: object, as_of: datetime) -> bool:
    if value is None:
        return True  # missing baseline rows are already explicitly blocked
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= as_of


def _missing_assessment_id(ticker: str, reason: str, as_of: datetime) -> str:
    canonical = f"P5|{ticker}|{reason}|{as_of.isoformat()}"
    return "4e1-" + sha256(canonical.encode()).hexdigest()[:16]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    as_of = datetime(2026, 7, 26, 23, 59, 59, tzinfo=timezone.utc)
    store = DatabaseStore(root / "data/audit.duckdb")
    gate = ValuationReadinessGate()
    results = []
    for ticker in ("MGLU3", "SUZB3", "KLBN11", "RAIL3", "SLCE3"):
        baseline_row = store.connection.execute(
            """
            SELECT baseline_id,ticker,cvm_code,as_of_timestamp,latest_quarter,
                   baseline_payload,field_evidence,missing_fields,confidence,
                   methodology_version,run_id,created_at
            FROM financial_baseline_snapshots WHERE ticker=? AND as_of_timestamp<=?
            ORDER BY as_of_timestamp DESC, created_at DESC LIMIT 1
            """,
            [ticker, as_of.replace(tzinfo=None)],
        ).fetchone()
        if baseline_row is None:
            results.append({"assessment_id": _missing_assessment_id(ticker, "MISSING_BASELINE", as_of),
                            "ticker": ticker, "status": "VALUATION_BLOCKED_MISSING_BASELINE",
                            "as_of_timestamp": as_of.isoformat(),
                            "valuation_eligible": False, "dcf_eligible": False,
                            "blockers": ["MISSING_BASELINE"], "evidence_ids": [],
                            "fair_value": None, "price_target": None,
                            "run_id": "valuation_p5_readiness"})
            continue
        baseline = _baseline(baseline_row)
        cal_rows = store.connection.execute(
            "SELECT calibration_payload FROM financial_bridge_calibrations WHERE ticker=? ORDER BY created_at DESC",
            [ticker],
        ).fetchall()
        calibrations = []
        calibration_validation_errors = []
        for row in cal_rows[:4]:
            try:
                calibrations.append(BridgeCalibrationResult.model_validate(json.loads(row[0])))
            except Exception as exc:  # legacy payloads are not valuation inputs
                calibration_validation_errors.append(type(exc).__name__)
        norm_row = store.connection.execute(
            "SELECT snapshot_payload FROM normalized_cash_flow_snapshots WHERE ticker=? ORDER BY created_at DESC LIMIT 1",
            [ticker],
        ).fetchone()
        if norm_row is None:
            results.append({"assessment_id": _missing_assessment_id(ticker, "MISSING_NORMALIZED_FCF", as_of),
                            "ticker": ticker, "status": "VALUATION_BLOCKED_MISSING_NORMALIZED_FCF",
                            "as_of_timestamp": as_of.isoformat(),
                            "valuation_eligible": False, "dcf_eligible": False,
                            "blockers": ["MISSING_NORMALIZED_FCF"], "evidence_ids": [],
                            "fair_value": None, "price_target": None,
                            "run_id": "valuation_p5_readiness"})
            continue
        normalized = NormalizedCashFlowSnapshot.model_validate(json.loads(norm_row[0]))
        market_row = store.connection.execute(
            """
            SELECT snapshot_payload FROM market_snapshots_pit
            WHERE ticker=? AND available_at<=? AND as_of_timestamp<=?
            ORDER BY as_of_timestamp DESC LIMIT 1
            """,
            [ticker, as_of.replace(tzinfo=None), as_of.replace(tzinfo=None)],
        ).fetchone()
        market = MarketSnapshotPIT.model_validate(json.loads(market_row[0])) if market_row else None
        assessment = gate.assess(
            baseline=baseline, calibrations=calibrations,
            normalized_cash_flow=normalized, market_snapshot=market,
            run_id="valuation_p5_readiness", as_of_timestamp=as_of,
        )
        payload = assessment.model_dump(mode="json")
        if calibration_validation_errors:
            payload["blockers"] = sorted(set(payload["blockers"] + ["CALIBRATION_SCHEMA_INVALID"]))
            payload["reasons"] = payload["reasons"] + [
                "historical calibration payload does not satisfy the current valuation schema"
            ]
            payload["status"] = "VALUATION_BLOCKED_CALIBRATION_SCHEMA_INVALID"
            payload["valuation_eligible"] = False
            payload["dcf_eligible"] = False
            payload["calibration_validation_errors"] = calibration_validation_errors
        payload["fair_value"] = None
        payload["price_target"] = None
        payload["multiple_classification"] = "DESCRIPTIVE_ONLY"
        results.append(payload)
    # Persist explicitly blocked companies as first-class assessment records too.
    for payload in results:
        if payload.get("assessment_id"):
            store.save_valuation_readiness_assessment(payload)
    store.close()
    output = {
        "sprint": "P5",
        "methodology_version": "P5-valuation-readiness-pilot-v1",
        "as_of_timestamp": as_of.isoformat(),
        "results": results,
        "safety": {"dcf_executed": False, "fair_values": False, "price_targets": False,
                    "buy_signals": False, "orders": False},
        "acceptance_checks": {
            "formal_gate_present": all("status" in item for item in results),
            "fcf_proxy_blocked": all("FCF_NOT_READY" in item.get("blockers", [])
                                      or "MISSING_NORMALIZED_FCF" in item.get("blockers", [])
                                      for item in results),
            "no_fair_value_or_price_target": all(item.get("fair_value") is None
                                                  and item.get("price_target") is None for item in results),
            "pit_cutoff": all(_is_pit_timestamp(item.get("as_of_timestamp"), as_of)
                               for item in results),
        },
        "valuation_status": "BLOCKED_UNTIL_P4_CALIBRATION_AND_FCF_READY",
    }
    path = root / "data/audits/valuation_p5_readiness.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
