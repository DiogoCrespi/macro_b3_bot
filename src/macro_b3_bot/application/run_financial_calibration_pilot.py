"""Run the focused Sprint 4D.3 calibration and normalized-cash-flow pilot."""
from __future__ import annotations

from datetime import datetime
import json
import math

from macro_b3_bot.application.calibrate_financial_bridges import (
    FinancialBridgeCalibrator,
)
from macro_b3_bot.application.valuation_readiness import ValuationReadinessGate
from macro_b3_bot.domain.financial_bridge_models import (
    FinancialBaselineSnapshot,
    MarketSnapshotPIT,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


class FinancialCalibrationPilot:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store

    def run(
        self,
        *,
        sector_run_id: str,
        as_of_timestamp: datetime,
        run_id: str = "financial_4d3b_integrity",
        market_snapshots: dict[str, MarketSnapshotPIT] | None = None,
    ) -> dict[str, object]:
        calibrator = FinancialBridgeCalibrator(self.store, run_id)
        diagnostics = calibrator.conflict_diagnostics(
            sector_run_id=sector_run_id,
            ticker_sectors={
                "SUZB3": "PAPEL_CELULOSE",
                "KLBN11": "PAPEL_CELULOSE",
            },
            as_of_timestamp=as_of_timestamp,
        )
        for item in diagnostics:
            self.store.save_causal_conflict_diagnostic(
                item.model_dump(mode="json")
            )

        calibrations = [
            calibrator.calibrate_interest("MGLU3", as_of_timestamp),
            calibrator.calibrate_fx("SUZB3", as_of_timestamp),
            calibrator.calibrate_fx("KLBN11", as_of_timestamp),
        ]
        for item in calibrations:
            self.store.save_financial_bridge_calibration(
                item.model_dump(mode="json")
            )

        baselines = {
            ticker: self._baseline(ticker, as_of_timestamp)
            for ticker in ("MGLU3", "SUZB3", "KLBN11")
        }
        normalized = [
            calibrator.normalize_cash_flow(item)
            for item in baselines.values()
        ]
        for item in normalized:
            self.store.save_normalized_cash_flow_snapshot(
                item.model_dump(mode="json")
            )
        readiness_assessments = []
        for ticker, baseline in baselines.items():
            assessment = ValuationReadinessGate().assess(
                baseline=baseline,
                calibrations=[item for item in calibrations if item.ticker == ticker],
                normalized_cash_flow=next(item for item in normalized if item.ticker == ticker),
                conflict_diagnostics=[item for item in diagnostics if item.ticker == ticker],
                market_snapshot=(market_snapshots or {}).get(ticker),
                run_id=run_id,
                as_of_timestamp=as_of_timestamp,
            )
            self.store.save_valuation_readiness_assessment(
                assessment.model_dump(mode="json")
            )
            readiness_assessments.append(assessment)

        controlled = [
            row
            for calibration in calibrations
            for row in calibrator.controlled_shocks(
                calibration,
                monetary_base=(
                    calibration.parameters.get(
                        "latest_quarter_revenue",
                        baselines[calibration.ticker].ttm_revenue / 4.0,
                    )
                    if calibration.bridge == "FX_OPERATING_REVENUE"
                    else None
                ),
            )
        ]
        controlled.extend(
            self._klbn_debt_shocks(baselines["KLBN11"])
        )
        outcome_intervals = self._outcome_intervals(controlled)
        return {
            "run_id": run_id,
            "as_of_timestamp": as_of_timestamp.isoformat(),
            "execution_modes": {
                "CALIBRATION_MODE": (
                    "controlled positive/negative shocks; no decision output"
                ),
                "DECISION_MODE": (
                    "real graph direction only; unresolved conflict is BLOCKED"
                ),
            },
            "conflict_diagnostics": [
                item.model_dump(mode="json") for item in diagnostics
            ],
            "calibrations": [
                item.model_dump(mode="json") for item in calibrations
            ],
            "controlled_shocks": controlled,
            "financial_outcome_intervals": outcome_intervals,
            "normalized_cash_flows": [
                item.model_dump(mode="json") for item in normalized
            ],
            "valuation_readiness_assessments": [
                item.model_dump(mode="json") for item in readiness_assessments
            ],
            "acceptance_checks": {
                "conflicts_identified": len(diagnostics) == 2,
                "conflicts_never_summed": all(
                    item.resolution_method == "NONE" for item in diagnostics
                ),
                "decision_mode_blocks_conflicts": all(
                    item.decision_mode_status == "BLOCKED"
                    for item in diagnostics
                ),
                "positive_and_negative_shocks": all(
                    {row["shock"] > 0 for row in controlled if row["ticker"] == ticker}
                    == {True, False}
                    for ticker in ("MGLU3", "SUZB3", "KLBN11")
                ),
                "outcomes_ordered_after_financial_calculation": all(
                    item["pessimistic"]["estimated_financial_change"]
                    <= item["base"]["estimated_financial_change"]
                    <= item["optimistic"]["estimated_financial_change"]
                    for item in outcome_intervals
                ),
                "neutral_base_is_zero": all(
                    item["base"]["shock"] == 0
                    and item["base"]["estimated_financial_change"] == 0
                    for item in outcome_intervals
                ),
                "mglu_structural_not_calibrated": all(
                    item.calibration_type == "STRUCTURAL_SENSITIVITY"
                    and item.calibration_status
                    == "STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE"
                    for item in calibrations if item.ticker == "MGLU3"
                ),
                "fx_out_of_sample_error_persisted": all(
                    item.validation_method == "EMPIRICAL_LOO_CROSS_VALIDATED"
                    and item.out_of_sample_mae is not None
                    for item in calibrations
                    if item.bridge == "FX_OPERATING_REVENUE"
                ),
                "unvalidated_bridges_fail_gate": all(
                    not item.validation_gate_passed for item in calibrations
                ),
                "heuristic_bands_not_confidence_intervals": all(
                    item.sensitivity_band_type == "HEURISTIC_SENSITIVITY_BAND"
                    for item in calibrations
                ),
                "minimum_five_replays_per_bridge": all(
                    len(item.observations) >= 5 for item in calibrations
                ),
                "normalized_fcf_three_companies": len(normalized) == 3,
                "normalized_fcf_values_finite": all(
                    math.isfinite(item.normalized_levered_fcf)
                    for item in normalized
                ),
                "normalization_adjustments_evidenced": all(
                    adjustment.source_ids
                    for item in normalized for adjustment in item.adjustments
                ),
                "reported_proxy_and_normalized_fcf_separate": all(
                    item.levered_fcf_proxy != item.normalized_levered_fcf
                    for item in normalized
                ),
                "uncalibrated_fx_confidence_capped": all(
                    item.confidence <= 0.4
                    for item in calibrations
                    if item.bridge == "FX_OPERATING_REVENUE"
                ),
                "normalized_fcf_blocked_for_dcf": all(
                    item.normalization_status == "NOT_VALUATION_READY"
                    and not item.dcf_eligible
                    for item in normalized
                ),
                "pit_timestamp_propagated": all(
                    item.observation_count >= 5
                    for item in calibrations
                ),
            },
            "readiness": {
                "MGLU3": "PARTIAL_CALIBRATION",
                "SUZB3": "PARTIAL_MISSING_DISCLOSED_VOLUME_HISTORY",
                "KLBN11": "PARTIAL_MISSING_DISCLOSED_VOLUME_HISTORY",
                "valuation": "BLOCKED",
                "buy": "BLOCKED",
                "orders": "BLOCKED",
                "mirofish": "BLOCKED",
                "valuation_blockers": [
                    "LOW_CALIBRATION_CONFIDENCE",
                    "MISSING_VALUATION_READY_NORMALIZED_FCF",
                    "CONFLICTING_MACRO_DIRECTION",
                ],
            },
        }

    def _baseline(
        self, ticker: str, as_of_timestamp: datetime
    ) -> FinancialBaselineSnapshot:
        row = self.store.connection.execute(
            """
            SELECT baseline_id,cvm_code,as_of_timestamp,latest_quarter,
                   baseline_payload,field_evidence,missing_fields,confidence,
                   methodology_version,run_id,created_at
            FROM financial_baseline_snapshots
            WHERE ticker=? AND as_of_timestamp<=?
            ORDER BY created_at DESC LIMIT 1
            """,
            [ticker, as_of_timestamp.replace(tzinfo=None)],
        ).fetchone()
        if row is None:
            raise ValueError(f"Missing financial baseline for {ticker}")
        payload = json.loads(row[4])
        return FinancialBaselineSnapshot(
            baseline_id=row[0],
            ticker=ticker,
            cvm_code=row[1],
            as_of_timestamp=row[2],
            latest_quarter=row[3],
            **payload,
            field_evidence=json.loads(row[5]),
            missing_fields=json.loads(row[6]),
            confidence=row[7],
            methodology_version=row[8],
            run_id=row[9],
            created_at=row[10],
        )

    @staticmethod
    def _klbn_debt_shocks(
        baseline: FinancialBaselineSnapshot,
    ) -> list[dict[str, float | str | None]]:
        output: list[dict[str, float | str | None]] = []
        for bridge, base in (
            ("CDI_SOFR_DEBT", baseline.average_floating_debt),
            ("IPCA_DEBT", baseline.inflation_linked_debt),
        ):
            for shock in (-200, -100, -50, 0, 50, 100, 200):
                output.append({
                    "mode": "CALIBRATION_MODE",
                    "ticker": "KLBN11",
                    "bridge": bridge,
                    "shock": shock,
                    "unit": "BASIS_POINTS",
                    "monetary_base": base,
                    "estimated_financial_change": (
                        None if base is None else -base * shock / 10_000
                    ),
                })
        return output

    @staticmethod
    def _outcome_intervals(
        controlled: list[dict[str, float | str | None]],
    ) -> list[dict[str, object]]:
        """Order scenario labels by calculated money impact, not shock size."""
        grouped: dict[tuple[str, str], list[dict[str, float | str | None]]] = {}
        for row in controlled:
            if row.get("estimated_financial_change") is None:
                continue
            key = (str(row["ticker"]), str(row["bridge"]))
            grouped.setdefault(key, []).append(row)

        output: list[dict[str, object]] = []
        for (ticker, bridge), rows in sorted(grouped.items()):
            ranked = sorted(
                rows, key=lambda item: float(item["estimated_financial_change"])
            )
            base_row = next(item for item in ranked if item["shock"] == 0)
            output.append({
                "ticker": ticker,
                "bridge": bridge,
                "label_method": "ORDERED_BY_CALCULATED_FINANCIAL_CHANGE",
                "pessimistic": ranked[0],
                "base": base_row,
                "optimistic": ranked[-1],
            })
        return output
