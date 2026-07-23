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
    case: Literal["PESSIMISTIC", "BASE", "OPTIMISTIC"]
    magnitude: float
    unit: Literal["PERCENT_CHANGE", "BASIS_POINTS", "PERCENTAGE_POINTS"]
    horizon_years: float = Field(gt=0)
    as_of_timestamp: datetime
    premise_source: Literal["USER_SPECIFIED_SCENARIO", "CONFIGURED_PILOT_ASSUMPTION"]
    assumption_ids: list[str] = Field(default_factory=list)
    methodology_version: str


class FinancialBridgeContribution(BaseModel):
    factor: str
    channel: Literal["revenue", "cost", "debt", "demand"]
    bridge_type: str
    scenario_id: str
    source_candidate_id: str
    exposure_field: str
    exposure_value: float
    monetary_base_field: str
    monetary_base_value: float
    shock_magnitude: float
    shock_unit: str
    horizon_years: float
    formula: str
    assumptions: dict[str, float] = Field(default_factory=dict)
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
