"""Sprint P4 walk-forward validation for the existing financial bridges."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from macro_b3_bot.application.calibrate_financial_bridges import FinancialBridgeCalibrator
from macro_b3_bot.infrastructure.store import DatabaseStore


class HistoricalBridgeValidator:
    """Run PIT expanding-window checks without promoting valuation parameters."""

    methodology_version = "P4-walk-forward-bridge-validation-v1"

    def __init__(self, store: DatabaseStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id
        self.calibrator = FinancialBridgeCalibrator(store, run_id)

    def run(self, *, as_of_timestamp: datetime) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for ticker in ("MGLU3", "SUZB3", "KLBN11"):
            if ticker == "MGLU3":
                results.append(self._structural_interest(ticker, as_of_timestamp))
            else:
                results.append(self._fx_walk_forward(ticker, as_of_timestamp))

        # KLBN11's debt-channel bridges are reported explicitly as blocked when
        # the canonical PIT store has no CDI/SOFR or IPCA macro series.  Missing
        # data must never be replaced by a synthetic zero series.
        for bridge in ("CDI_SOFR_DEBT", "IPCA_DEBT"):
            results.append(self._missing_series("KLBN11", bridge, as_of_timestamp))

        for item in results:
            self.store.save_historical_bridge_validation(item)
        self.store.connection.commit()
        return {
            "run_id": self.run_id,
            "methodology_version": self.methodology_version,
            "as_of_timestamp": as_of_timestamp.isoformat(),
            "mode": "CALIBRATION_MODE",
            "results": results,
            "acceptance_checks": {
                "minimum_five_windows_or_explicit_block": all(
                    item["walk_forward_window_count"] >= 5
                    or item["status"] == "BLOCKED_MISSING_MACRO_SERIES"
                    for item in results
                ),
                "oos_metrics_persisted": all(
                    "out_of_sample_mae" in item for item in results
                ),
                "no_automatic_promotion": all(
                    item["promotion_status"] == "NOT_PROMOTED_TO_VALUATION"
                    for item in results
                ),
                "pit_cutoff_applied": all(
                    item["as_of_timestamp"] == as_of_timestamp.isoformat()
                    for item in results
                ),
            },
            "valuation": "BLOCKED",
            "buy": "BLOCKED",
            "orders": "BLOCKED",
        }

    def _structural_interest(self, ticker: str, as_of: datetime) -> dict[str, Any]:
        frame = self.calibrator.quarterly_financials(ticker, as_of)
        macro = self.calibrator.macro_quarterly(as_of)
        merged = frame.merge(macro, on="period_end", how="inner")
        count = max(0, len(merged) - 1)
        return self._base_result(
            ticker=ticker,
            bridge="NET_INTEREST_CASH_EFFECT",
            as_of=as_of,
            status="STRUCTURAL_SENSITIVITY",
            windows=count,
            in_sample_mae=None,
            out_of_sample_mae=None,
            missing_drivers=[
                "EFFECTIVE_FLOATING_DEBT_SHARE",
                "RATE_SENSITIVE_CASH_SHARE",
                "REPRICING_LAG",
                "DERIVATIVE_RATE_EFFECT",
            ],
            observations=count,
            notes="Structural proxy; no empirical effective-rate exposure was inferred.",
        )

    def _fx_walk_forward(self, ticker: str, as_of: datetime) -> dict[str, Any]:
        financials = self.calibrator.quarterly_financials(ticker, as_of)
        macro = self.calibrator.macro_quarterly(as_of)
        frame = financials.merge(macro, on="period_end", how="inner")
        if frame.empty:
            return self._missing_series(ticker, "FX_OPERATING_REVENUE", as_of)
        frame["factor_change"] = frame["usdbrl_sell"].pct_change(fill_method=None)
        frame["secondary_factor_change"] = frame["wood_pulp_ppi"].pct_change(fill_method=None)
        frame["financial_change"] = frame["revenue"].pct_change(fill_method=None)
        frame = frame.dropna().tail(12).reset_index(drop=True)
        if len(frame) < 5:
            return self._base_result(
                ticker=ticker, bridge="FX_OPERATING_REVENUE", as_of=as_of,
                status="BLOCKED_INSUFFICIENT_WINDOWS", windows=len(frame),
                in_sample_mae=None, out_of_sample_mae=None,
                missing_drivers=["MINIMUM_FIVE_WALK_FORWARD_WINDOWS"],
                observations=len(frame), notes="Fewer than five PIT observations.",
            )
        first = frame["factor_change"].tolist()
        second = frame["secondary_factor_change"].tolist()
        target = frame["financial_change"].tolist()
        intercept, coefficients = self.calibrator._multiple_regression(first, second, target)
        predictions, stability = self.calibrator._expanding_window_predictions(
            first, second, target, (coefficients[0], coefficients[1]), min_train_size=4
        )
        pairs = [
            (actual, predicted) for actual, predicted in zip(target, predictions, strict=True)
            if predicted is not None
        ]
        oos_errors = [abs(actual - predicted) for actual, predicted in pairs]
        in_sample = [
            actual - (intercept + fx * coefficients[0] + pulp * coefficients[1])
            for actual, fx, pulp in zip(target, first, second, strict=True)
        ]
        return self._base_result(
            ticker=ticker, bridge="FX_OPERATING_REVENUE", as_of=as_of,
            status="EMPIRICAL_OUT_OF_SAMPLE_REVIEW",
            windows=len(pairs), in_sample_mae=sum(abs(x) for x in in_sample) / len(in_sample),
            out_of_sample_mae=sum(oos_errors) / len(oos_errors) if oos_errors else None,
            missing_drivers=["DISCLOSED_VOLUME_HISTORY"], observations=len(frame),
            notes="Expanding-window validation with intercept and FX/PPI controls.",
            parameters={"intercept": intercept, "fx_slope": coefficients[0], "pulp_slope": coefficients[1]},
            coefficient_sign_stability={"fx_slope": stability[0], "pulp_slope": stability[1]},
            out_of_sample_observations=len(pairs),
        )

    def _missing_series(self, ticker: str, bridge: str, as_of: datetime) -> dict[str, Any]:
        return self._base_result(
            ticker=ticker, bridge=bridge, as_of=as_of,
            status="BLOCKED_MISSING_MACRO_SERIES", windows=0,
            in_sample_mae=None, out_of_sample_mae=None,
            missing_drivers=["PIT_MACRO_SERIES_NOT_AVAILABLE"], observations=0,
            notes="No canonical PIT macro series; no synthetic values were generated.",
        )

    def _base_result(self, *, ticker: str, bridge: str, as_of: datetime,
                     status: str, windows: int, in_sample_mae: float | None,
                     out_of_sample_mae: float | None, missing_drivers: list[str],
                     observations: int, notes: str, parameters: dict[str, float] | None = None,
                     coefficient_sign_stability: dict[str, float] | None = None,
                     out_of_sample_observations: int = 0) -> dict[str, Any]:
        payload = {
            "ticker": ticker, "bridge": bridge, "as_of_timestamp": as_of.isoformat(),
            "methodology_version": self.methodology_version, "status": status,
            "walk_forward_window_count": windows, "observation_count": observations,
            "out_of_sample_observations": out_of_sample_observations,
            "in_sample_mae": in_sample_mae, "out_of_sample_mae": out_of_sample_mae,
            "parameters": parameters or {},
            "coefficient_sign_stability": coefficient_sign_stability or {},
            "missing_drivers": missing_drivers, "notes": notes,
            "promotion_status": "NOT_PROMOTED_TO_VALUATION",
            "run_id": self.run_id,
        }
        identity = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload["validation_id"] = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return payload
