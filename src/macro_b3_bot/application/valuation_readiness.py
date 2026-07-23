"""Sprint 4E.1 valuation-readiness gate.

This module deliberately has no DCF or price-target path.  It emits an
auditable refusal (or, only when every gate passes, a readiness decision) and
keeps observed market multiples explicitly descriptive.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Iterable

from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    FactorConflictDiagnostic,
    FinancialBaselineSnapshot,
    FinancialScenarioOutcome,
    NormalizedCashFlowSnapshot,
    ValuationReadinessAssessment,
)


class ValuationReadinessGate:
    methodology_version = "4E.1-valuation-readiness-gate-v1"

    def assess(
        self,
        *,
        baseline: FinancialBaselineSnapshot,
        calibrations: Iterable[BridgeCalibrationResult],
        normalized_cash_flow: NormalizedCashFlowSnapshot,
        conflict_diagnostics: Iterable[FactorConflictDiagnostic] = (),
        scenario_outcomes: Iterable[FinancialScenarioOutcome] = (),
        market_data: dict[str, Any] | None = None,
        run_id: str = "valuation_readiness",
        as_of_timestamp: datetime | None = None,
    ) -> ValuationReadinessAssessment:
        calibrations = list(calibrations)
        conflicts = list(conflict_diagnostics)
        outcomes = list(scenario_outcomes)
        market_data = market_data or {}
        blockers: list[str] = []
        reasons: list[str] = []

        if not calibrations or any(
            not item.validation_gate_passed
            or item.confidence < 0.6
            or item.calibration_status != "COMPANY_CALIBRATED"
            for item in calibrations
        ):
            blockers.append("LOW_CALIBRATION_CONFIDENCE")
            reasons.append("4D calibration gate did not establish company-calibrated parameters")
        if any(
            item.validation_failures
            and any("OUT_OF_SAMPLE" in failure or "VALIDATION" in failure
                    for failure in item.validation_failures)
            for item in calibrations
        ):
            blockers.append("EMPIRICAL_VALIDATION")
            reasons.append("empirical validation is insufficient for valuation use")
        if normalized_cash_flow.dcf_eligible is not True or normalized_cash_flow.normalization_status != "VALUATION_READY":
            blockers.append("FCF_NOT_READY")
            reasons.append("normalized cash flow is a statistical proxy and is not DCF eligible")
        if any(item.decision_mode_status == "BLOCKED" for item in conflicts):
            blockers.append("CONFLICTING_MACRO_DIRECTION")
            reasons.append("macro factor direction remains unresolved in decision mode")
        if not market_data or market_data.get("price") is None:
            blockers.append("MISSING_MARKET_DATA")
            reasons.append("current price and/or share-count market data is unavailable")
        if any(item.observation_count < 5 for item in calibrations) or not baseline.field_evidence:
            blockers.append("INSUFFICIENT_HISTORY")
            reasons.append("historical observations or baseline evidence are insufficient")

        primary = self._primary_status(blockers)
        descriptive = self._descriptive_metrics(baseline, market_data)
        timestamp = as_of_timestamp or baseline.as_of_timestamp
        identity = f"{baseline.ticker}|{timestamp.isoformat()}|{run_id}"
        assessment_id = "4e1-" + sha256(identity.encode()).hexdigest()[:16]
        inputs = {
            "baseline_id": baseline.baseline_id,
            "calibration_ids": [item.calibration_id for item in calibrations],
            "normalized_cash_flow_id": normalized_cash_flow.snapshot_id,
            "diagnostic_ids": [item.diagnostic_id for item in conflicts],
            "scenario_outcome_ids": [item.outcome_id for item in outcomes],
            "market_data_fields": sorted(market_data),
            "dcf_blocked": True,
        }
        return ValuationReadinessAssessment(
            assessment_id=assessment_id,
            ticker=baseline.ticker,
            as_of_timestamp=timestamp,
            status=primary,
            valuation_eligible=False,
            dcf_eligible=False,
            blockers=blockers,
            reasons=reasons,
            evidence_ids=[evidence for item in baseline.field_evidence for evidence in item.source_ids],
            inputs=inputs,
            descriptive_metrics=descriptive,
            methodology_version=self.methodology_version,
            run_id=run_id,
        )

    @staticmethod
    def _primary_status(blockers: list[str]) -> str:
        if not blockers:
            return "VALUATION_READY"
        order = (
            ("LOW_CALIBRATION_CONFIDENCE", "VALUATION_BLOCKED_LOW_CALIBRATION_CONFIDENCE"),
            ("FCF_NOT_READY", "VALUATION_BLOCKED_FCF_NOT_READY"),
            ("CONFLICTING_MACRO_DIRECTION", "VALUATION_BLOCKED_CONFLICTING_MACRO_DIRECTION"),
            ("MISSING_MARKET_DATA", "VALUATION_BLOCKED_MISSING_MARKET_DATA"),
            ("INSUFFICIENT_HISTORY", "VALUATION_BLOCKED_INSUFFICIENT_HISTORY"),
        )
        return next(status for code, status in order if code in blockers)

    @staticmethod
    def _descriptive_metrics(
        baseline: FinancialBaselineSnapshot, market_data: dict[str, Any]
    ) -> dict[str, Any]:
        price = market_data.get("price")
        shares = market_data.get("shares_outstanding")
        market_cap = price * shares if price is not None and shares is not None else None
        ev = market_cap + baseline.net_debt if market_cap is not None else None
        def ratio(numerator: float | None, denominator: float | None) -> float | None:
            return None if numerator is None or denominator in (None, 0) else numerator / denominator
        return {
            "market_capitalization": {"value": market_cap},
            "enterprise_value": {"value": ev},
            "pe_observed": {"value": ratio(market_cap, baseline.ttm_net_income)},
            "ev_ebitda_observed": {"value": ratio(ev, baseline.ttm_ebitda)},
            "p_fcf_proxy_observed": {"value": ratio(market_cap, baseline.ttm_fcf)},
        }

