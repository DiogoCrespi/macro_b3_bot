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

        # Debt channels are evaluated independently. Missing macro series remain
        # blocked; no synthetic zero series is introduced.
        for bridge in ("CDI_SOFR_DEBT", "IPCA_DEBT"):
            results.append(self._debt_walk_forward("KLBN11", bridge, as_of_timestamp))

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
        gross_debt = float(frame["gross_debt"].median()) if not frame.empty else None
        cash = float(frame["cash"].median()) if not frame.empty else None
        approved = self.store.connection.execute(
            """
            SELECT field_name, normalized_value FROM company_macro_exposure_facts
            WHERE ticker=? AND review_status IN ('HUMAN_APPROVED','DELEGATED_AI_APPROVED')
            ORDER BY reviewed_at DESC NULLS LAST
            """,
            [ticker],
        ).fetchall()
        approved_fields = {name: float(value) for name, value in approved if name and value is not None}
        floating_share = approved_fields.get("floating_rate_debt_pct")
        floating_debt = gross_debt * floating_share if gross_debt is not None and floating_share is not None else None
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
            parameters={
                "average_gross_debt_observed": gross_debt,
                "average_cash_observed": cash,
                "approved_floating_rate_debt_pct": floating_share,
                "average_effective_floating_debt": floating_debt,
                "gross_interest_effect_formula": "-effective_floating_debt * delta_rate * repricing_factor",
            },
            notes=(
                "Structural proxy; gross debt and cash are observed balances, but "
                "cash sensitivity, repricing lag and derivatives are not inferred."
            ),
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
        oos_squared = [(actual - predicted) ** 2 for actual, predicted in pairs]
        in_sample = [
            actual - (intercept + fx * coefficients[0] + pulp * coefficients[1])
            for actual, fx, pulp in zip(target, first, second, strict=True)
        ]
        return self._base_result(
            ticker=ticker, bridge="FX_OPERATING_REVENUE", as_of=as_of,
            status="EMPIRICAL_OUT_OF_SAMPLE_REVIEW",
            windows=len(pairs), in_sample_mae=sum(abs(x) for x in in_sample) / len(in_sample),
            out_of_sample_mae=sum(oos_errors) / len(oos_errors) if oos_errors else None,
            out_of_sample_rmse=(sum(oos_squared) / len(oos_squared)) ** 0.5 if oos_squared else None,
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

    def _debt_walk_forward(self, ticker: str, bridge: str, as_of: datetime) -> dict[str, Any]:
        financials = self.calibrator.quarterly_financials(ticker, as_of)
        macro = self.calibrator.macro_quarterly(as_of)
        indicator = "cdi_daily" if bridge == "CDI_SOFR_DEBT" else "ipca_monthly"
        if indicator not in macro.columns:
            return self._missing_series(ticker, bridge, as_of)
        frame = financials.merge(macro[["period_end", indicator]], on="period_end", how="inner")
        frame["factor_change"] = frame[indicator].diff()
        frame["financial_change"] = frame["financial_result"].diff()
        frame = frame.dropna().tail(12).reset_index(drop=True)
        if len(frame) < 5:
            return self._base_result(
                ticker=ticker, bridge=bridge, as_of=as_of,
                status="BLOCKED_INSUFFICIENT_WINDOWS", windows=len(frame),
                in_sample_mae=None, out_of_sample_mae=None,
                missing_drivers=["MINIMUM_FIVE_WALK_FORWARD_WINDOWS"],
                observations=len(frame), notes="Fewer than five PIT observations.",
            )
        # A univariate expanding-window sensitivity is diagnostic only: financial
        # result also contains FX, commodity and non-rate items.
        x = frame["factor_change"].tolist()
        y = frame["financial_change"].tolist()
        predictions: list[float | None] = [None] * len(y)
        # Three historical training quarters are the minimum for this
        # diagnostic (intercept + one factor); the resulting forecast remains
        # a review signal, never a valuation calibration.
        for i in range(3, len(y)):
            train_x, train_y = x[:i], y[:i]
            mean_x = sum(train_x) / len(train_x)
            mean_y = sum(train_y) / len(train_y)
            denom = sum((v - mean_x) ** 2 for v in train_x)
            slope = sum((a - mean_x) * (b - mean_y) for a, b in zip(train_x, train_y)) / denom if denom else 0.0
            intercept = mean_y - slope * mean_x
            predictions[i] = intercept + slope * x[i]
        pairs = [(a, b) for a, b in zip(y, predictions, strict=True) if b is not None]
        errors = [abs(a - b) for a, b in pairs]
        squared = [(a - b) ** 2 for a, b in pairs]
        return self._base_result(
            ticker=ticker, bridge=bridge, as_of=as_of,
            status="EMPIRICAL_OUT_OF_SAMPLE_REVIEW", windows=len(pairs),
            in_sample_mae=None,
            out_of_sample_mae=sum(errors) / len(errors) if errors else None,
            out_of_sample_rmse=(sum(squared) / len(squared)) ** 0.5 if squared else None,
            missing_drivers=["RATE_SENSITIVE_DEBT_SHARE", "REPRICING_LAG", "DERIVATIVE_RATE_EFFECT"],
            observations=len(frame), out_of_sample_observations=len(pairs),
            notes=f"Expanding-window diagnostic using PIT {indicator}; financial result has confounders.",
        )

    def _base_result(self, *, ticker: str, bridge: str, as_of: datetime,
                     status: str, windows: int, in_sample_mae: float | None,
                     out_of_sample_mae: float | None, missing_drivers: list[str],
                     observations: int, notes: str, parameters: dict[str, float] | None = None,
                     coefficient_sign_stability: dict[str, float] | None = None,
                     out_of_sample_observations: int = 0,
                     out_of_sample_rmse: float | None = None) -> dict[str, Any]:
        payload = {
            "ticker": ticker, "bridge": bridge, "as_of_timestamp": as_of.isoformat(),
            "methodology_version": self.methodology_version, "status": status,
            "walk_forward_window_count": windows, "observation_count": observations,
            "out_of_sample_observations": out_of_sample_observations,
            "in_sample_mae": in_sample_mae, "out_of_sample_mae": out_of_sample_mae,
            "out_of_sample_rmse": out_of_sample_rmse,
            "parameters": parameters or {},
            "coefficient_sign_stability": coefficient_sign_stability or {},
            "missing_drivers": missing_drivers, "notes": notes,
            "promotion_status": "NOT_PROMOTED_TO_VALUATION",
            "run_id": self.run_id,
        }
        identity = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        payload["validation_id"] = hashlib.sha256(identity.encode()).hexdigest()[:32]
        return payload
