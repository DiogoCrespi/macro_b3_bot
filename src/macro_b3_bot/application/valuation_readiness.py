"""Sprint 4E.1 valuation-readiness gate.

This module deliberately has no DCF or price-target path.  It emits an
auditable refusal (or, only when every gate passes, a readiness decision) and
keeps observed market multiples explicitly descriptive.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
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
        timestamp = as_of_timestamp or baseline.as_of_timestamp
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
        market_data_ready, market_data_reason = self._market_data_is_pit(
            market_data, timestamp
        )
        if not market_data_ready:
            blockers.append("MISSING_MARKET_DATA")
            reasons.append(market_data_reason)
        if any(item.observation_count < 5 for item in calibrations) or not baseline.field_evidence:
            blockers.append("INSUFFICIENT_HISTORY")
            reasons.append("historical observations or baseline evidence are insufficient")

        primary = self._primary_status(blockers)
        valuation_eligible = not blockers
        dcf_eligible = valuation_eligible and normalized_cash_flow.dcf_eligible is True
        descriptive = self._descriptive_metrics(baseline, market_data)
        input_identity = {
            "ticker": baseline.ticker,
            "as_of": timestamp.isoformat(),
            "baseline_id": baseline.baseline_id,
            "calibration_ids": sorted(item.calibration_id for item in calibrations),
            "normalized_cash_flow_id": normalized_cash_flow.snapshot_id,
            "diagnostic_ids": sorted(item.diagnostic_id for item in conflicts),
            "scenario_outcome_ids": sorted(item.outcome_id for item in outcomes),
            "market_data_version": market_data.get("market_data_version"),
            "methodology_version": self.methodology_version,
        }
        identity = json.dumps(input_identity, sort_keys=True, separators=(",", ":"))
        assessment_id = "4e1-" + sha256(identity.encode()).hexdigest()[:16]
        inputs = {
            "baseline_id": baseline.baseline_id,
            "calibration_ids": [item.calibration_id for item in calibrations],
            "normalized_cash_flow_id": normalized_cash_flow.snapshot_id,
            "diagnostic_ids": [item.diagnostic_id for item in conflicts],
            "scenario_outcome_ids": [item.outcome_id for item in outcomes],
            "market_data_fields": sorted(market_data),
            "market_data_version": market_data.get("market_data_version"),
            "market_data_source_id": market_data.get("source_id"),
            "market_data_as_of": market_data.get("as_of", market_data.get("market_data_as_of")),
            "dcf_blocked": not dcf_eligible,
            "input_identity": input_identity,
        }
        return ValuationReadinessAssessment(
            assessment_id=assessment_id,
            ticker=baseline.ticker,
            as_of_timestamp=timestamp,
            status=primary,
            valuation_eligible=valuation_eligible,
            dcf_eligible=dcf_eligible,
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
            ("EMPIRICAL_VALIDATION", "VALUATION_BLOCKED_EMPIRICAL_VALIDATION"),
            ("FCF_NOT_READY", "VALUATION_BLOCKED_FCF_NOT_READY"),
            ("CONFLICTING_MACRO_DIRECTION", "VALUATION_BLOCKED_CONFLICTING_MACRO_DIRECTION"),
            ("MISSING_MARKET_DATA", "VALUATION_BLOCKED_MISSING_MARKET_DATA"),
            ("INSUFFICIENT_HISTORY", "VALUATION_BLOCKED_INSUFFICIENT_HISTORY"),
        )
        return next(status for code, status in order if code in blockers)

    @staticmethod
    def _market_data_is_pit(
        market_data: dict[str, Any], assessment_timestamp: datetime
    ) -> tuple[bool, str]:
        price = market_data.get("price")
        shares = market_data.get("shares_outstanding")
        if price is None or shares is None or float(shares) <= 0:
            return False, "PIT market data requires positive price and shares_outstanding"
        if not market_data.get("source_id"):
            return False, "PIT market data source_id is required"
        if not market_data.get("currency"):
            return False, "PIT market data currency is required"
        if not market_data.get("market_data_version"):
            return False, "PIT market_data_version is required"
        available = market_data.get("available_at")
        as_of = market_data.get("as_of", market_data.get("market_data_as_of"))
        if available is None or as_of is None:
            return False, "PIT market data requires available_at and as_of"
        try:
            available_dt = ValuationReadinessGate._parse_timestamp(available)
            as_of_dt = ValuationReadinessGate._parse_timestamp(as_of)
        except (TypeError, ValueError):
            return False, "PIT market timestamps must be ISO datetime values"
        if available_dt > assessment_timestamp or as_of_dt > assessment_timestamp:
            return False, "market data is not point-in-time valid for the assessment"
        return True, ""

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise TypeError("unsupported timestamp")

    @staticmethod
    def _descriptive_metrics(
        baseline: FinancialBaselineSnapshot, market_data: dict[str, Any]
    ) -> dict[str, Any]:
        price = market_data.get("price")
        shares = market_data.get("shares_outstanding")
        market_cap = price * shares if price is not None and shares is not None and float(shares) > 0 else None
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
