"""Auditable point-in-time financial bridge contracts (Sprint 4D.1)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FinancialFieldEvidence(BaseModel):
    field_name: str
    source_ids: list[str] = Field(min_length=1)
    source_locations: list[str] = Field(min_length=1)
    available_at: list[datetime] = Field(min_length=1)
    period_end: date
    currency: str = "BRL"
    unit: str = "BRL"
    formula: str
    components: dict[str, float] = Field(default_factory=dict)
    evidence_label: Literal[
        "fact_source_reported", "derived_calculation", "missing_required_source"
    ]
    confidence: float = Field(ge=0, le=1)
    notes: str | None = None


class FinancialBaselineSnapshot(BaseModel):
    baseline_id: str
    ticker: str
    cvm_code: str
    as_of_timestamp: datetime
    latest_quarter: date
    currency: str = "BRL"
    unit: str = "BRL"
    methodology_version: str
    fcf_definition: Literal["CFO_PLUS_REPORTED_CAPEX"] = "CFO_PLUS_REPORTED_CAPEX"
    fcf_normalization_status: Literal["NOT_NORMALIZED"] = "NOT_NORMALIZED"
    average_debt_method: Literal["TWO_POINT_AVERAGE_PROXY"] = "TWO_POINT_AVERAGE_PROXY"
    net_debt_method: Literal["STANDARDIZED_CASH_ONLY"] = "STANDARDIZED_CASH_ONLY"

    ttm_revenue: float
    ttm_costs: float
    ttm_ebit: float
    ttm_ebitda: float | None = None
    ttm_financial_result: float
    ttm_pre_tax_income: float
    ttm_net_income: float
    ttm_operating_cash_flow: float
    ttm_capex: float
    ttm_fcf: float
    gross_debt: float
    cash: float
    net_debt: float
    average_gross_debt: float
    average_floating_debt: float | None = None
    average_net_fx_debt: float | None = None
    inflation_linked_debt: float | None = None
    effective_tax_rate: float | None = Field(default=None, ge=0, le=1)
    working_capital: float

    field_evidence: list[FinancialFieldEvidence] = Field(min_length=1)
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    run_id: str
    created_at: datetime

    @model_validator(mode="after")
    def every_value_is_evidenced(self) -> "FinancialBaselineSnapshot":
        evidenced = {item.field_name for item in self.field_evidence}
        excluded = {
            "baseline_id", "ticker", "cvm_code", "as_of_timestamp",
            "latest_quarter", "currency", "unit", "methodology_version",
            "fcf_definition", "fcf_normalization_status",
            "average_debt_method", "net_debt_method",
            "field_evidence", "missing_fields", "confidence", "run_id",
            "created_at",
        }
        for name in self.__class__.model_fields:
            if name not in excluded and getattr(self, name) is not None and name not in evidenced:
                raise ValueError(f"{name} has a value without financial evidence")
        return self


class EconomicShockScenario(BaseModel):
    scenario_id: str
    factor: Literal[
        "FX", "INTEREST_RATES", "INFLATION", "OIL", "ECONOMIC_ACTIVITY"
    ]
    shock_case: Literal["LOW_SHOCK", "BASE_SHOCK", "HIGH_SHOCK"]
    direction: Literal[-1, 1]
    absolute_magnitude: float = Field(ge=0)
    signed_magnitude: float
    unit: Literal["PERCENT_CHANGE", "BASIS_POINTS", "PERCENTAGE_POINTS"]
    horizon_years: float = Field(gt=0)
    as_of_timestamp: datetime
    premise_source: Literal["USER_SPECIFIED_SCENARIO", "CONFIGURED_PILOT_ASSUMPTION"]
    assumption_ids: list[str] = Field(default_factory=list)
    methodology_version: str

    @model_validator(mode="after")
    def signed_magnitude_is_consistent(self) -> "EconomicShockScenario":
        expected = self.direction * self.absolute_magnitude
        if abs(self.signed_magnitude - expected) > 1e-12:
            raise ValueError("signed_magnitude must equal direction * absolute_magnitude")
        return self


class FinancialBridgeContribution(BaseModel):
    factor: str
    channel: Literal["revenue", "cost", "debt", "demand"]
    factor_direction: Literal[-1, 1]
    channel_effect_direction: Literal[-1, 1]
    bridge_type: str
    scenario_id: str
    source_candidate_id: str
    exposure_field: str
    exposure_value: float
    monetary_base_field: str
    monetary_base_value: float
    causal_direction: Literal[-1, 1]
    absolute_shock_magnitude: float
    signed_shock_magnitude: float
    shock_unit: str
    horizon_years: float
    formula: str
    assumptions: dict[str, float] = Field(default_factory=dict)
    assumption_calibration_status: Literal[
        "COMPANY_CALIBRATED", "ASSUMPTION_NOT_COMPANY_CALIBRATED"
    ] = "ASSUMPTION_NOT_COMPANY_CALIBRATED"
    accounting_fx_revaluation: float = 0
    cash_interest_effect: float = 0
    cash_principal_effect: float = 0
    hedge_settlement_effect: float = 0
    delta_revenue: float = 0
    delta_ebitda: float = 0
    delta_ebit: float = 0
    delta_financial_result: float = 0
    delta_pre_tax_income: float = 0
    delta_net_income: float = 0
    delta_operating_cash_flow: float = 0
    delta_fcf: float = 0
    delta_net_debt: float = 0
    confidence: float = Field(ge=0, le=1)
    causal_evidence_status: str
    exposure_evidence_ids: list[str] = Field(default_factory=list)
    baseline_evidence_ids: list[str] = Field(default_factory=list)


class BlockedFinancialChannel(BaseModel):
    factor: str
    channel: str
    reason: Literal[
        "BRIDGE_BLOCKED_MISSING_ELASTICITY",
        "BRIDGE_BLOCKED_MISSING_MONETARY_BASE",
        "BRIDGE_BLOCKED_MISSING_NET_EXPOSURE",
        "BRIDGE_BLOCKED_NO_ACTIVE_SIGNAL",
        "BRIDGE_BLOCKED_NO_ACTIVE_CAUSAL_FACTOR",
        "BRIDGE_BLOCKED_UNSUPPORTED_FACTOR_CHANNEL",
        "SCENARIO_BLOCKED_CONFLICTING_FACTOR_DIRECTION",
    ]
    required_fields: list[str] = Field(default_factory=list)


class FinancialScenarioMetrics(BaseModel):
    revenue: float
    ebitda: float | None = None
    ebit: float
    financial_result: float
    pre_tax_income: float
    net_income: float
    operating_cash_flow: float
    fcf: float
    net_debt: float


class FinancialScenarioOutcome(BaseModel):
    outcome_id: str
    ticker: str
    case: Literal["PESSIMISTIC", "BASE", "OPTIMISTIC"]
    shock_case: Literal["LOW_SHOCK", "BASE_SHOCK", "HIGH_SHOCK"]
    as_of_timestamp: datetime
    baseline_id: str
    company_impact_candidate_id: str
    metrics: FinancialScenarioMetrics
    absolute_changes: FinancialScenarioMetrics
    percentage_changes: dict[str, float | None]
    margins: dict[str, float | None]
    contributions: list[FinancialBridgeContribution] = Field(default_factory=list)
    blocked_channels: list[BlockedFinancialChannel] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    status: Literal["CALCULATED", "NO_ACTION", "PARTIAL", "BLOCKED"]
    reason: str
    run_id: str


class CausalConflictPath(BaseModel):
    factor: str
    factor_direction: Literal[-1, 1]
    macro_event_id: str
    source_path_id: str
    causal_edge_ids: list[str]
    event_available_at: datetime
    horizon_days: int
    lag_days: int
    strength: float
    confidence: float
    evidence_status: str


class FactorConflictDiagnostic(BaseModel):
    diagnostic_id: str
    ticker: str
    sector: str
    factor: str
    as_of_timestamp: datetime
    paths: list[CausalConflictPath] = Field(min_length=2)
    classification: Literal[
        "PROBABLE_GRAPH_OR_PROPAGATION_DEFECT",
        "LEGITIMATE_COMPETING_HYPOTHESES",
    ]
    decision_mode_status: Literal["BLOCKED"]
    resolution_method: Literal["NONE"]
    run_id: str


class CashFlowNormalizationAdjustment(BaseModel):
    adjustment_id: str
    field_name: str
    value: float
    sign: Literal[-1, 1]
    period_end: date
    source_ids: list[str] = Field(min_length=1)
    rationale: str
    recurrence: Literal["RECURRING", "NON_RECURRING", "NORMALIZATION_PROXY"]
    confidence: float = Field(ge=0, le=1)
    formula: str


class NormalizedCashFlowSnapshot(BaseModel):
    snapshot_id: str
    ticker: str
    as_of_timestamp: datetime
    reported_operating_cash_flow: float
    reported_capex: float
    levered_fcf_proxy: float
    normalized_operating_cash_flow: float
    maintenance_capex: float
    normalized_levered_fcf: float
    statistical_normalized_fcf_proxy: float
    normalization_type: Literal["STATISTICAL_NORMALIZATION_PROXY"]
    normalization_status: Literal["NOT_VALUATION_READY"]
    dcf_eligible: Literal[False] = False
    adjustments: list[CashFlowNormalizationAdjustment] = Field(min_length=1)
    methodology_version: str
    confidence: float = Field(ge=0, le=1)
    run_id: str


class DescriptiveMarketMetric(BaseModel):
    """Observed multiple; never a fair-value or trading recommendation."""

    value: float | None = None
    classification: Literal["DESCRIPTIVE_ONLY"] = "DESCRIPTIVE_ONLY"
    not_a_fair_value: Literal[True] = True
    not_buy_eligible: Literal[True] = True


class ValuationReadinessAssessment(BaseModel):
    """Explicit readiness gate separating diagnostics from valuation."""

    assessment_id: str
    ticker: str
    as_of_timestamp: datetime
    status: Literal[
        "VALUATION_READY",
        "VALUATION_BLOCKED_LOW_CALIBRATION_CONFIDENCE",
        "VALUATION_BLOCKED_EMPIRICAL_VALIDATION",
        "VALUATION_BLOCKED_FCF_NOT_READY",
        "VALUATION_BLOCKED_CONFLICTING_MACRO_DIRECTION",
        "VALUATION_BLOCKED_MISSING_MARKET_DATA",
        "VALUATION_BLOCKED_INSUFFICIENT_HISTORY",
    ]
    valuation_eligible: bool
    dcf_eligible: bool
    blockers: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    inputs: dict[str, object] = Field(default_factory=dict)
    descriptive_metrics: dict[str, DescriptiveMarketMetric] = Field(default_factory=dict)
    methodology_version: str
    run_id: str

    @model_validator(mode="after")
    def eligibility_matches_status(self) -> "ValuationReadinessAssessment":
        ready = self.status == "VALUATION_READY"
        if self.valuation_eligible != ready:
            raise ValueError("valuation_eligible must match readiness status")
        if self.dcf_eligible and not self.valuation_eligible:
            raise ValueError("DCF cannot be eligible when valuation is blocked")
        return self


class BridgeReplayObservation(BaseModel):
    ticker: str
    bridge: str
    period_end: date
    factor_change: float
    secondary_factor_change: float | None = None
    financial_change: float
    predicted_change: float
    error: float
    out_of_sample_predicted_change: float | None = None
    out_of_sample_error: float | None = None
    source_ids: list[str] = Field(min_length=1)


class BridgeCalibrationResult(BaseModel):
    calibration_id: str
    ticker: str
    bridge: str
    mode: Literal["CALIBRATION_MODE"]
    observations: list[BridgeReplayObservation] = Field(min_length=5)
    parameters: dict[str, float]
    # Retained for serialized compatibility. These are not confidence intervals.
    parameter_ranges: dict[str, list[float]]
    heuristic_sensitivity_band: dict[str, list[float]]
    sensitivity_band_type: Literal["HEURISTIC_SENSITIVITY_BAND"]
    mean_absolute_error: float
    in_sample_mae: float
    out_of_sample_mae: float | None = None
    validation_method: Literal[
        "STRUCTURAL_FORMULA_ONLY",
        "LEAVE_ONE_OUT",
        "EMPIRICAL_LOO_CROSS_VALIDATED",
        "EXPANDING_WINDOW_WALK_FORWARD",
    ]
    coefficient_sign_stability: dict[str, float] = Field(default_factory=dict)
    observation_count: int = Field(ge=5)
    calibration_type: Literal[
        "STRUCTURAL_SENSITIVITY",
        "EMPIRICAL_IN_SAMPLE",
        "EMPIRICAL_LOO_CROSS_VALIDATED",
        "EMPIRICAL_OUT_OF_SAMPLE_VALIDATED",
    ]
    calibration_horizon: Literal["QUARTERLY", "ANNUAL"] = "QUARTERLY"
    financial_target_period: Literal["QUARTERLY", "ANNUAL", "TTM"] = "QUARTERLY"
    monetary_base_period: Literal["QUARTERLY", "ANNUAL", "TTM"] = "QUARTERLY"
    annualization_method: Literal[
        "NONE", "ANNUALIZED_4X", "ANNUAL_CONVERTED"
    ] = "NONE"
    validation_gate_passed: bool
    validation_failures: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    calibration_status: Literal[
        "COMPANY_CALIBRATED",
        "PARTIAL_MISSING_DRIVER",
        "STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE",
    ]
    missing_drivers: list[str] = Field(default_factory=list)
    methodology_version: str
    run_id: str
