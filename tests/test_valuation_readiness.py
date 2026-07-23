from datetime import date, datetime, timezone

from macro_b3_bot.application.valuation_readiness import ValuationReadinessGate
from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    BridgeReplayObservation,
    CashFlowNormalizationAdjustment,
    FinancialBaselineSnapshot,
    FinancialFieldEvidence,
    NormalizedCashFlowSnapshot,
)


AS_OF = datetime(2026, 7, 22, tzinfo=timezone.utc)


def _baseline() -> FinancialBaselineSnapshot:
    fields = []
    for name in (
        "ttm_revenue", "ttm_costs", "ttm_ebit", "ttm_financial_result",
        "ttm_pre_tax_income", "ttm_net_income", "ttm_operating_cash_flow",
        "ttm_capex", "ttm_fcf", "gross_debt", "cash", "net_debt",
        "average_gross_debt", "working_capital",
    ):
        fields.append(FinancialFieldEvidence(
            field_name=name, source_ids=[f"src-{name}"],
            source_locations=["cvm"], available_at=[AS_OF],
            period_end=date(2026, 3, 31), formula="reported", evidence_label="fact_source_reported", confidence=1,
        ))
    return FinancialBaselineSnapshot(
        baseline_id="base", ticker="MGLU3", cvm_code="1", as_of_timestamp=AS_OF,
        latest_quarter=date(2026, 3, 31), methodology_version="test",
        ttm_revenue=1000, ttm_costs=700, ttm_ebit=100, ttm_financial_result=-10,
        ttm_pre_tax_income=90, ttm_net_income=70, ttm_operating_cash_flow=120,
        ttm_capex=-40, ttm_fcf=80, gross_debt=200, cash=50, net_debt=150,
        average_gross_debt=210, working_capital=20, field_evidence=fields,
        confidence=.9, run_id="test", created_at=AS_OF,
    )


def _calibration() -> BridgeCalibrationResult:
    rows = [BridgeReplayObservation(
        ticker="MGLU3", bridge="FX", period_end=date(2025, 3, 31),
        factor_change=.1, financial_change=1, predicted_change=1, error=0,
        source_ids=["cvm"],
    ) for _ in range(5)]
    return BridgeCalibrationResult(
        calibration_id="cal", ticker="MGLU3", bridge="FX", mode="CALIBRATION_MODE",
        observations=rows, parameters={}, parameter_ranges={}, heuristic_sensitivity_band={},
        sensitivity_band_type="HEURISTIC_SENSITIVITY_BAND", mean_absolute_error=0,
        in_sample_mae=0, validation_method="EXPANDING_WINDOW_WALK_FORWARD",
        observation_count=5, calibration_type="COMPANY_CALIBRATED", validation_gate_passed=True,
        confidence=.9, calibration_status="COMPANY_CALIBRATED", methodology_version="test", run_id="test",
    )


def _fcf() -> NormalizedCashFlowSnapshot:
    adjustment = CashFlowNormalizationAdjustment(
        adjustment_id="a", field_name="ocf", value=1, sign=1,
        period_end=date(2026, 3, 31), source_ids=["cvm"], rationale="test",
        recurrence="NORMALIZATION_PROXY", confidence=.5, formula="x",
    )
    return NormalizedCashFlowSnapshot(
        snapshot_id="fcf", ticker="MGLU3", as_of_timestamp=AS_OF,
        reported_operating_cash_flow=120, reported_capex=-40, levered_fcf_proxy=80,
        normalized_operating_cash_flow=100, maintenance_capex=-40, normalized_levered_fcf=60,
        statistical_normalized_fcf_proxy=60, normalization_type="STATISTICAL_NORMALIZATION_PROXY",
        normalization_status="NOT_VALUATION_READY", adjustments=[adjustment],
        methodology_version="test", confidence=.4, run_id="test",
    )


def test_gate_blocks_low_confidence_and_fcf_and_labels_multiples() -> None:
    result = ValuationReadinessGate().assess(
        baseline=_baseline(), calibrations=[_calibration().model_copy(update={"validation_gate_passed": False, "confidence": .2})],
        normalized_cash_flow=_fcf(), market_data={"price": 10, "shares_outstanding": 100},
    )
    assert result.status == "VALUATION_BLOCKED_LOW_CALIBRATION_CONFIDENCE"
    assert {"LOW_CALIBRATION_CONFIDENCE", "FCF_NOT_READY"} <= set(result.blockers)
    assert result.valuation_eligible is False and result.dcf_eligible is False
    assert result.descriptive_metrics["market_capitalization"].not_a_fair_value is True
    assert result.descriptive_metrics["pe_observed"].classification == "DESCRIPTIVE_ONLY"


def test_gate_flags_missing_market_data_and_persists_identity() -> None:
    result = ValuationReadinessGate().assess(
        baseline=_baseline(), calibrations=[_calibration()], normalized_cash_flow=_fcf(), run_id="r",
    )
    assert "MISSING_MARKET_DATA" in result.blockers
    assert result.assessment_id.startswith("4e1-")

