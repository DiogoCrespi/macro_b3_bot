"""Sprint 4D.3 retrospective bridge calibration and cash-flow normalization."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from statistics import mean, median

import pandas as pd
import numpy as np

from macro_b3_bot.application.evaluate_sector_impacts import CausalGraphEngine
from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    BridgeReplayObservation,
    CashFlowNormalizationAdjustment,
    CausalConflictPath,
    FactorConflictDiagnostic,
    FinancialBaselineSnapshot,
    NormalizedCashFlowSnapshot,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


class FinancialBridgeCalibrator:
    """Calibrate only from observed financial/macro histories; never make decisions."""

    methodology_version = "4D.3A-calibration-validity-gate-v1"

    def __init__(self, store: DatabaseStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def conflict_diagnostics(
        self,
        *,
        sector_run_id: str,
        ticker_sectors: dict[str, str],
        as_of_timestamp: datetime,
    ) -> list[FactorConflictDiagnostic]:
        graph = CausalGraphEngine(self.store, f"{self.run_id}_graph_metadata")
        edges = {edge.edge_id: edge for edge in graph.edges}
        output: list[FactorConflictDiagnostic] = []
        for ticker, sector in ticker_sectors.items():
            rows = self.store.connection.execute(
                """
                SELECT event_id,event_available_at,horizon_days,causal_paths
                FROM sector_impact_candidates
                WHERE run_id=? AND sector=? AND as_of_timestamp<=?
                """,
                [sector_run_id, sector, as_of_timestamp.replace(tzinfo=None)],
            ).fetchall()
            by_factor: dict[str, list[CausalConflictPath]] = defaultdict(list)
            for event_id, available_at, horizon_days, payload in rows:
                for path in json.loads(payload):
                    edge_ids = path["causal_edge_ids"]
                    lag = sum(edges[item].lag_days for item in edge_ids)
                    horizon = min(
                        [edges[item].horizon_days for item in edge_ids]
                        or [horizon_days]
                    )
                    by_factor[path["factor"]].append(CausalConflictPath(
                        factor=path["factor"],
                        factor_direction=int(path["factor_direction"]),
                        macro_event_id=event_id,
                        source_path_id=path["path_id"],
                        causal_edge_ids=edge_ids,
                        event_available_at=available_at,
                        horizon_days=horizon,
                        lag_days=lag,
                        strength=float(path["strength"]),
                        confidence=float(path["confidence"]),
                        evidence_status=path["evidence_status"],
                    ))
            for factor, paths in by_factor.items():
                if len({item.factor_direction for item in paths}) < 2:
                    continue
                event_horizons: dict[tuple[str, int], set[int]] = defaultdict(set)
                for item in paths:
                    event_horizons[
                        (item.macro_event_id, item.horizon_days)
                    ].add(item.factor_direction)
                same_event_conflict = any(
                    len(directions) > 1
                    for directions in event_horizons.values()
                )
                identity = (
                    f"{self.run_id}|{ticker}|{sector}|{factor}|"
                    f"{as_of_timestamp.isoformat()}"
                )
                output.append(FactorConflictDiagnostic(
                    diagnostic_id=hashlib.sha256(
                        identity.encode()
                    ).hexdigest()[:24],
                    ticker=ticker,
                    sector=sector,
                    factor=factor,
                    as_of_timestamp=as_of_timestamp,
                    paths=sorted(
                        paths,
                        key=lambda item: (
                            item.macro_event_id,
                            item.source_path_id,
                            item.factor_direction,
                        ),
                    ),
                    classification=(
                        "PROBABLE_GRAPH_OR_PROPAGATION_DEFECT"
                        if same_event_conflict
                        else "LEGITIMATE_COMPETING_HYPOTHESES"
                    ),
                    decision_mode_status="BLOCKED",
                    resolution_method="NONE",
                    run_id=self.run_id,
                ))
        return output

    def quarterly_financials(self, ticker: str) -> pd.DataFrame:
        cvm_code = self.store.connection.execute(
            """
            SELECT cvm_code FROM company_ticker_map
            WHERE ticker=? AND validated=TRUE ORDER BY valid_from DESC LIMIT 1
            """,
            [ticker],
        ).fetchone()[0]
        rows = self.store.connection.execute(
            """
            SELECT d.document_id,d.document_type,d.reference_date,
                   l.statement_type,l.account_code,
                   CAST(l.value AS DOUBLE)*l.scale AS value
            FROM cvm_documents d
            JOIN financial_statement_lines l ON l.document_id=d.document_id
            WHERE d.cvm_code=? AND l.scope='CONSOLIDATED'
              AND l.fiscal_order='ÚLTIMO'
              AND (
                (l.statement_type='DRE' AND l.account_code IN ('3.01','3.05','3.06'))
                OR (l.statement_type LIKE 'DFC%' AND l.account_code='6.01')
                OR (l.statement_type='BPA' AND l.account_code='1.01.01')
                OR (l.statement_type='BPP' AND l.account_code IN ('2.01.04','2.02.01'))
              )
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.document_id,l.statement_type,l.account_code
                ORDER BY
                    CASE WHEN d.document_type='ITR' THEN l.start_date END DESC,
                    CASE WHEN d.document_type='DFP' THEN l.start_date END ASC
            )=1
            """,
            [cvm_code],
        ).fetchdf()
        if rows.empty:
            return rows
        wide = (
            rows.pivot_table(
                index=["document_id", "document_type", "reference_date"],
                columns="account_code",
                values="value",
                aggfunc="sum",
            )
            .reset_index()
            .sort_values("reference_date")
        )
        output: list[dict[str, object]] = []
        year_quarters: dict[int, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        prior_cfo_ytd: dict[int, float] = {}
        for item in wide.to_dict("records"):
            period = item["reference_date"]
            year = period.year
            values = {
                "revenue": float(item.get("3.01", 0) or 0),
                "ebit": float(item.get("3.05", 0) or 0),
                "financial_result": float(item.get("3.06", 0) or 0),
                "operating_cash_flow": float(item.get("6.01", 0) or 0),
            }
            balance_values = {
                "cash": float(item.get("1.01.01", 0) or 0),
                "gross_debt": (
                    float(item.get("2.01.04", 0) or 0)
                    + float(item.get("2.02.01", 0) or 0)
                ),
            }
            if item["document_type"] == "ITR":
                quarter = dict(values)
                quarter["operating_cash_flow"] = (
                    values["operating_cash_flow"]
                    - prior_cfo_ytd.get(year, 0)
                )
                prior_cfo_ytd[year] = values["operating_cash_flow"]
                for key, value in quarter.items():
                    year_quarters[year][key] += value
            else:
                quarter = {
                    key: value - year_quarters[year].get(key, 0)
                    for key, value in values.items()
                }
            output.append({
                "period_end": period,
                "document_id": item["document_id"],
                **quarter,
                **balance_values,
            })
        result = pd.DataFrame(output).sort_values("period_end")
        result["period_end"] = pd.to_datetime(result["period_end"]).dt.date
        return result

    def macro_quarterly(self) -> pd.DataFrame:
        rows = self.store.connection.execute(
            """
            SELECT reference_date,indicator,CAST(value AS DOUBLE) AS value
            FROM macro_observations
            WHERE indicator IN (
                'usdbrl_sell','selic_annualized_252','wood_pulp_ppi'
            )
              AND reference_date BETWEEN DATE '2022-12-01' AND DATE '2026-07-22'
            """
        ).fetchdf()
        if rows.empty:
            return rows
        rows["period_end"] = pd.to_datetime(rows["reference_date"]).dt.to_period(
            "Q"
        ).dt.end_time.dt.date
        return (
            rows.groupby(["period_end", "indicator"])["value"]
            .mean()
            .unstack()
            .reset_index()
            .sort_values("period_end")
        )

    def calibrate_interest(self, ticker: str) -> BridgeCalibrationResult:
        financials = self.quarterly_financials(ticker)
        macro = self.macro_quarterly()
        frame = financials.merge(macro, on="period_end", how="inner")
        frame["factor_change"] = frame["selic_annualized_252"].diff() / 100
        frame["financial_change"] = frame["financial_result"].diff()
        frame = frame.dropna().tail(12)
        frame["effective_exposure"] = frame["gross_debt"] - frame["cash"]
        frame["predicted_change"] = (
            -frame["effective_exposure"] * frame["factor_change"] * 0.25
        )
        return self._calibration_from_predictions(
            ticker,
            "NET_INTEREST_CASH_EFFECT",
            frame,
            parameters={
                "observed_slope": float(
                    -frame["effective_exposure"].median()
                ),
                "average_gross_debt_proxy": float(
                    frame["gross_debt"].median()
                ),
                "average_standardized_cash_proxy": float(
                    frame["cash"].median()
                ),
                "quarter_horizon": 0.25,
            },
            missing_drivers=[
                "EFFECTIVE_FLOATING_DEBT_SHARE",
                "RATE_SENSITIVE_CASH_SHARE",
                "REPRICING_LAG",
                "DERIVATIVE_RATE_EFFECT",
                "USABLE_TAX_SHIELD",
            ],
            calibration_type="STRUCTURAL_SENSITIVITY",
        )

    def calibrate_fx(self, ticker: str) -> BridgeCalibrationResult:
        financials = self.quarterly_financials(ticker)
        macro = self.macro_quarterly()
        frame = financials.merge(macro, on="period_end", how="inner")
        frame["factor_change"] = frame["usdbrl_sell"].pct_change(fill_method=None)
        frame["secondary_factor_change"] = frame["wood_pulp_ppi"].pct_change(
            fill_method=None
        )
        frame["financial_change"] = frame["revenue"].pct_change(fill_method=None)
        frame = frame.dropna().tail(12)
        intercept, coefficients = self._multiple_regression(
            frame["factor_change"].tolist(),
            frame["secondary_factor_change"].tolist(),
            frame["financial_change"].tolist(),
        )
        frame["predicted_change"] = (
            intercept
            + frame["factor_change"] * coefficients[0]
            + frame["secondary_factor_change"] * coefficients[1]
        )
        oos_predictions, sign_stability = self._leave_one_out_predictions(
            frame["factor_change"].tolist(),
            frame["secondary_factor_change"].tolist(),
            frame["financial_change"].tolist(),
            coefficients,
        )
        frame["out_of_sample_predicted_change"] = oos_predictions
        frame["out_of_sample_error"] = (
            frame["financial_change"] - frame["out_of_sample_predicted_change"]
        )
        return self._calibration_from_predictions(
            ticker,
            "FX_OPERATING_REVENUE",
            frame,
            parameters={
                "intercept": intercept,
                "fx_observed_slope": coefficients[0],
                "pulp_price_observed_slope": coefficients[1],
            },
            missing_drivers=["DISCLOSED_VOLUME_HISTORY"],
            calibration_type="EMPIRICAL_IN_SAMPLE",
            validation_method="LEAVE_ONE_OUT",
            coefficient_sign_stability={
                "fx_observed_slope": sign_stability[0],
                "pulp_price_observed_slope": sign_stability[1],
            },
        )

    def normalize_cash_flow(
        self,
        baseline: FinancialBaselineSnapshot,
    ) -> NormalizedCashFlowSnapshot:
        history = self.quarterly_financials(baseline.ticker)
        usable_history = history.dropna(subset=["operating_cash_flow"]).tail(8)
        cfo_values = usable_history["operating_cash_flow"].tolist()
        if len(cfo_values) < 5:
            raise ValueError(
                f"{baseline.ticker} has fewer than five CFO replay periods"
            )
        annualized_median_cfo = median(cfo_values) * 4
        cfo_adjustment = annualized_median_cfo - baseline.ttm_operating_cash_flow
        maintenance_capex = abs(baseline.ttm_capex)
        source_ids = sorted({
            source
            for evidence in baseline.field_evidence
            if evidence.field_name in {"ttm_operating_cash_flow", "ttm_capex"}
            for source in evidence.source_ids
        } | set(usable_history["document_id"].astype(str).tolist()))
        adjustments = [
            CashFlowNormalizationAdjustment(
                adjustment_id=hashlib.sha256(
                    f"{baseline.baseline_id}|CFO_MEDIAN".encode()
                ).hexdigest()[:24],
                field_name="normalized_operating_cash_flow",
                value=abs(cfo_adjustment),
                sign=1 if cfo_adjustment >= 0 else -1,
                period_end=baseline.latest_quarter,
                source_ids=source_ids,
                rationale=(
                    "Replace reported TTM CFO with the annualized median of "
                    "the latest eight standalone quarters to damp exceptional "
                    "working-capital and financial-arm volatility."
                ),
                recurrence="NORMALIZATION_PROXY",
                confidence=0.55,
                formula="median(last_8_quarter_reported_cfo) * 4 - reported_ttm_cfo",
            ),
            CashFlowNormalizationAdjustment(
                adjustment_id=hashlib.sha256(
                    f"{baseline.baseline_id}|MAINTENANCE_CAPEX".encode()
                ).hexdigest()[:24],
                field_name="maintenance_capex",
                value=maintenance_capex,
                sign=-1,
                period_end=baseline.latest_quarter,
                source_ids=source_ids,
                rationale=(
                    "Use reported TTM capex as a conservative maintenance-capex "
                    "proxy until issuer growth/maintenance disclosure is available."
                ),
                recurrence="NORMALIZATION_PROXY",
                confidence=0.50,
                formula="abs(reported_ttm_capex)",
            ),
        ]
        normalized_fcf = annualized_median_cfo - maintenance_capex
        return NormalizedCashFlowSnapshot(
            snapshot_id=hashlib.sha256(
                f"{self.run_id}|{baseline.baseline_id}|normalized_fcf".encode()
            ).hexdigest()[:24],
            ticker=baseline.ticker,
            as_of_timestamp=baseline.as_of_timestamp,
            reported_operating_cash_flow=baseline.ttm_operating_cash_flow,
            reported_capex=baseline.ttm_capex,
            levered_fcf_proxy=baseline.ttm_fcf,
            normalized_operating_cash_flow=annualized_median_cfo,
            maintenance_capex=maintenance_capex,
            normalized_levered_fcf=normalized_fcf,
            statistical_normalized_fcf_proxy=normalized_fcf,
            normalization_type="STATISTICAL_NORMALIZATION_PROXY",
            normalization_status="NOT_VALUATION_READY",
            dcf_eligible=False,
            adjustments=adjustments,
            methodology_version=self.methodology_version,
            confidence=0.30,
            run_id=self.run_id,
        )

    def controlled_shocks(
        self,
        calibration: BridgeCalibrationResult,
        monetary_base: float | None = None,
    ) -> list[dict[str, float | str]]:
        values = (
            [-200, -100, -50, 0, 50, 100, 200]
            if calibration.bridge == "NET_INTEREST_CASH_EFFECT"
            else [-20, -10, -5, 0, 5, 10, 20]
        )
        divisor = 10_000 if calibration.bridge == "NET_INTEREST_CASH_EFFECT" else 100
        coefficient = next(
            value
            for key, value in calibration.parameters.items()
            if key in {"observed_slope", "fx_observed_slope"}
        )
        output: list[dict[str, float | str]] = []
        for value in values:
            estimated = coefficient * value / divisor
            if calibration.bridge == "FX_OPERATING_REVENUE":
                if monetary_base is None:
                    raise ValueError("FX calibration requires a revenue monetary base")
                estimated *= monetary_base
            row: dict[str, float | str] = {
                "mode": "CALIBRATION_MODE",
                "ticker": calibration.ticker,
                "bridge": calibration.bridge,
                "shock": value,
                "unit": (
                    "BASIS_POINTS"
                    if divisor == 10_000 else "PERCENT_CHANGE"
                ),
                "estimated_financial_change": estimated,
                "financial_change_unit": "BRL",
            }
            if calibration.bridge == "NET_INTEREST_CASH_EFFECT":
                rate = value / divisor
                horizon = calibration.parameters["quarter_horizon"]
                gross = (
                    -calibration.parameters["average_gross_debt_proxy"]
                    * rate * horizon
                )
                cash = (
                    calibration.parameters["average_standardized_cash_proxy"]
                    * rate * horizon
                )
                row.update({
                    "gross_debt_structural_proxy_effect": gross,
                    "standardized_cash_structural_proxy_offset": cash,
                    "derivative_effect": None,
                    "repricing_lag_factor": None,
                    "average_effective_exposure": -coefficient,
                    "tax_shield": None,
                    "net_interest_cash_effect": gross + cash,
                })
                row["estimated_financial_change"] = gross + cash
            output.append(row)
        return output

    def _calibration_from_predictions(
        self,
        ticker: str,
        bridge: str,
        frame: pd.DataFrame,
        parameters: dict[str, float],
        *,
        missing_drivers: list[str],
        calibration_type: str,
        validation_method: str = "STRUCTURAL_FORMULA_ONLY",
        coefficient_sign_stability: dict[str, float] | None = None,
    ) -> BridgeCalibrationResult:
        observations = [
            BridgeReplayObservation(
                ticker=ticker,
                bridge=bridge,
                period_end=row.period_end,
                factor_change=float(row.factor_change),
                financial_change=float(row.financial_change),
                secondary_factor_change=(
                    float(row.secondary_factor_change)
                    if hasattr(row, "secondary_factor_change") else None
                ),
                predicted_change=float(row.predicted_change),
                error=float(row.financial_change - row.predicted_change),
                out_of_sample_predicted_change=(
                    float(row.out_of_sample_predicted_change)
                    if hasattr(row, "out_of_sample_predicted_change") else None
                ),
                out_of_sample_error=(
                    float(row.out_of_sample_error)
                    if hasattr(row, "out_of_sample_error") else None
                ),
                source_ids=[
                    str(row.document_id),
                    (
                        "BCB_SGS:1178"
                        if bridge == "NET_INTEREST_CASH_EFFECT"
                        else "BCB_SGS:1"
                    ),
                    *(
                        ["FRED:WPU0911"]
                        if bridge == "FX_OPERATING_REVENUE" else []
                    ),
                ],
            )
            for row in frame.itertuples()
        ]
        mae = mean(abs(item.error) for item in observations)
        oos_errors = [
            abs(item.out_of_sample_error)
            for item in observations
            if item.out_of_sample_error is not None
        ]
        oos_mae = mean(oos_errors) if oos_errors else None
        scale = mean(abs(item.financial_change) for item in observations) or 1
        validation_mae = oos_mae if oos_mae is not None else mae
        confidence = min(
            0.4,
            max(0.05, (1 - min(1, validation_mae / scale)) * 0.4),
        )
        stability = coefficient_sign_stability or {}
        validation_failures = list(missing_drivers)
        if calibration_type == "STRUCTURAL_SENSITIVITY":
            validation_failures.append("NO_EMPIRICAL_PARAMETER_ESTIMATION")
        if validation_method == "LEAVE_ONE_OUT":
            if oos_mae is None:
                validation_failures.append("MISSING_OUT_OF_SAMPLE_ERROR")
            elif oos_mae / scale > 0.5:
                validation_failures.append("OUT_OF_SAMPLE_ERROR_TOO_HIGH")
            if stability and min(stability.values()) < 0.8:
                validation_failures.append("COEFFICIENT_SIGN_UNSTABLE")
        validation_gate_passed = not validation_failures
        effective_calibration_type = (
            "EMPIRICAL_OUT_OF_SAMPLE_VALIDATED"
            if validation_gate_passed and validation_method == "LEAVE_ONE_OUT"
            else calibration_type
        )
        identity = f"{self.run_id}|{ticker}|{bridge}"
        return BridgeCalibrationResult(
            calibration_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=ticker,
            bridge=bridge,
            mode="CALIBRATION_MODE",
            observations=observations,
            parameters=parameters,
            parameter_ranges={
                key: [value * 0.75, value, value * 1.25]
                for key, value in parameters.items()
            },
            heuristic_sensitivity_band={
                key: [value * 0.75, value, value * 1.25]
                for key, value in parameters.items()
            },
            sensitivity_band_type="HEURISTIC_SENSITIVITY_BAND",
            mean_absolute_error=mae,
            in_sample_mae=mae,
            out_of_sample_mae=oos_mae,
            validation_method=validation_method,
            coefficient_sign_stability=stability,
            observation_count=len(observations),
            calibration_type=effective_calibration_type,
            validation_gate_passed=validation_gate_passed,
            validation_failures=sorted(set(validation_failures)),
            confidence=confidence,
            calibration_status=(
                "STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE"
                if calibration_type == "STRUCTURAL_SENSITIVITY"
                else (
                    "PARTIAL_MISSING_DRIVER"
                    if missing_drivers else "COMPANY_CALIBRATED"
                )
            ),
            missing_drivers=missing_drivers,
            methodology_version=self.methodology_version,
            run_id=self.run_id,
        )

    @staticmethod
    def _slope(x: list[float], y: list[float]) -> float:
        x_mean, y_mean = mean(x), mean(y)
        denominator = sum((item - x_mean) ** 2 for item in x)
        if denominator == 0:
            return 0
        return sum(
            (x_item - x_mean) * (y_item - y_mean)
            for x_item, y_item in zip(x, y, strict=True)
        ) / denominator

    @staticmethod
    def _multiple_regression(
        first: list[float],
        second: list[float],
        target: list[float],
    ) -> tuple[float, tuple[float, float]]:
        matrix = np.column_stack([np.ones(len(first)), first, second])
        coefficients, *_ = np.linalg.lstsq(matrix, np.array(target), rcond=None)
        return (
            float(coefficients[0]),
            (float(coefficients[1]), float(coefficients[2])),
        )

    @classmethod
    def _leave_one_out_predictions(
        cls,
        first: list[float],
        second: list[float],
        target: list[float],
        full_coefficients: tuple[float, float],
    ) -> tuple[list[float], tuple[float, float]]:
        predictions: list[float] = []
        signs: list[list[bool]] = [[], []]
        for holdout in range(len(target)):
            train = [index for index in range(len(target)) if index != holdout]
            intercept, coefficients = cls._multiple_regression(
                [first[index] for index in train],
                [second[index] for index in train],
                [target[index] for index in train],
            )
            predictions.append(
                intercept
                + coefficients[0] * first[holdout]
                + coefficients[1] * second[holdout]
            )
            for index, coefficient in enumerate(coefficients):
                reference = full_coefficients[index]
                signs[index].append(
                    coefficient == 0 == reference
                    or coefficient * reference > 0
                )
        stability = tuple(
            sum(values) / len(values) for values in signs
        )
        return predictions, (float(stability[0]), float(stability[1]))
