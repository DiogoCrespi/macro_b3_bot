"""Point-in-time company exposure and impact contracts (Sprint 4C.1)."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ExtractionMethod(str, Enum):
    EXPLICIT_DISCLOSURE = "EXPLICIT_DISCLOSURE"
    STATEMENT_DERIVED = "STATEMENT_DERIVED"
    RULE_DERIVED = "RULE_DERIVED"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"
    AUDITED_OVERRIDE = "AUDITED_OVERRIDE"
    UNKNOWN = "UNKNOWN"


class ExposureFieldEvidence(BaseModel):
    field_name: str
    value: float | dict[str, float] | None = None
    source_type: str
    evidence_id: str
    evidence_excerpt: str | None = None
    available_at: datetime
    extraction_method: ExtractionMethod
    methodology_version: str
    confidence: float = Field(ge=0, le=1)
    is_estimated: bool = False
    rationale: str | None = None


class CompanyExposureSnapshot(BaseModel):
    exposure_id: str
    ticker: str
    cvm_code: str
    sector: str
    as_of_timestamp: datetime
    reference_date: date
    exposure_version: str

    total_revenue: float | None = None
    foreign_revenue: float | None = None
    total_debt: float | None = None
    gross_financial_debt: float | None = None
    foreign_currency_debt: float | None = None
    floating_rate_debt: float | None = None
    inflation_linked_debt: float | None = None

    revenue_foreign_currency_pct: float | None = Field(default=None, ge=0, le=1)
    cost_foreign_currency_pct: float | None = Field(default=None, ge=0, le=1)
    export_revenue_pct: float | None = Field(default=None, ge=0, le=1)
    floating_rate_debt_pct: float | None = Field(default=None, ge=0, le=1)
    inflation_linked_debt_pct: float | None = Field(default=None, ge=0, le=1)
    foreign_currency_debt_pct: float | None = Field(default=None, ge=0, le=1)
    commodity_exposures: dict[str, float] | None = None
    geographic_exposures: dict[str, float] | None = None
    demand_cyclicality: float | None = Field(default=None, ge=0, le=1)
    pricing_power: float | None = Field(default=None, ge=0, le=1)
    operating_leverage: float | None = Field(default=None, ge=0, le=1)

    field_evidence: list[ExposureFieldEvidence] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence_quality_score: float = Field(default=0, ge=0, le=1)
    completeness_score: float = Field(default=0, ge=0, le=1)
    run_id: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_semantics(self) -> "CompanyExposureSnapshot":
        if self.commodity_exposures and any(
            value < -1 or value > 1 for value in self.commodity_exposures.values()
        ):
            raise ValueError("commodity sensitivities must be between -1 and 1")
        if self.geographic_exposures:
            if any(value < 0 or value > 1 for value in self.geographic_exposures.values()):
                raise ValueError("geographic revenue shares must be between 0 and 1")
            if sum(self.geographic_exposures.values()) > 1.01:
                raise ValueError("geographic revenue shares cannot sum above 1")
        evidenced = {item.field_name for item in self.field_evidence}
        for field_name in self.__class__.model_fields:
            if field_name in {
                "exposure_id", "ticker", "cvm_code", "sector", "as_of_timestamp",
                "reference_date", "exposure_version", "field_evidence", "missing_fields",
                "confidence", "evidence_quality_score", "completeness_score",
                "run_id", "created_at",
            }:
                continue
            if getattr(self, field_name) is not None and field_name not in evidenced:
                raise ValueError(f"{field_name} has a value without field-level evidence")
        return self


class CompanyFactorChannel(BaseModel):
    factor: str
    channel: Literal["revenue", "cost", "debt", "demand"]
    direction: int = Field(ge=-1, le=1)
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    source_path_ids: list[str] = Field(min_length=1)
    causal_edge_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_status: Literal["VALIDATED", "HYPOTHESIS"]

    @model_validator(mode="after")
    def direction_is_signed(self) -> "CompanyFactorChannel":
        if self.direction not in {-1, 1}:
            raise ValueError("channel direction must be -1 or +1")
        return self


class CompanyExposureOverride(BaseModel):
    override_id: str
    ticker: str
    field_name: str
    previous_value: Any = None
    new_value: Any
    rationale: str = Field(min_length=10)
    evidence_ids: list[str] = Field(min_length=1)
    approved_by: str
    approved_at: datetime
    methodology_version: str
    run_id: str


class FactorContribution(BaseModel):
    factor: str
    channel: Literal["revenue", "cost", "debt", "demand"]
    raw_factor_impact: float = Field(ge=-1, le=1)
    exposure_field: str
    exposure_value: float
    exposure_confidence: float = Field(ge=0, le=1)
    modifier_fields: list[str] = Field(default_factory=list)
    modifier_methodology_version: str | None = None
    modifier_beta: float | None = None
    final_contribution: float = Field(ge=-1, le=1)
    source_path_ids: list[str] = Field(default_factory=list)
    causal_edge_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_status: Literal["VALIDATED", "HYPOTHESIS"]


class MissingFactorExposure(BaseModel):
    factor: str
    channel: Literal["revenue", "cost", "debt", "demand"]
    reason: Literal["NO_EXPOSURE_MAPPING", "MISSING_EXPOSURE_VALUE"]
    expected_fields: list[str] = Field(default_factory=list)


class CompanyImpactCandidate(BaseModel):
    candidate_id: str
    ticker: str
    sector_snapshot_id: str
    company_exposure_id: str
    as_of_timestamp: datetime
    revenue_impact_score: float | None = Field(default=None, ge=-1, le=1)
    cost_impact_score: float | None = Field(default=None, ge=-1, le=1)
    debt_impact_score: float | None = Field(default=None, ge=-1, le=1)
    demand_impact_score: float | None = Field(default=None, ge=-1, le=1)
    net_company_impact: float | None = Field(default=None, ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    conflict_ratio: float = Field(ge=0, le=1)
    supporting_event_ids: list[str] = Field(default_factory=list)
    opposing_event_ids: list[str] = Field(default_factory=list)
    source_path_ids: list[str] = Field(default_factory=list)
    causal_edge_ids: list[str] = Field(default_factory=list)
    factor_contributions: list[FactorContribution] = Field(default_factory=list)
    missing_factor_exposures: list[MissingFactorExposure] = Field(default_factory=list)
    unsupported_factor_channels: list[MissingFactorExposure] = Field(default_factory=list)
    causal_evidence_status: Literal["VALIDATED", "HYPOTHESIS"]
    missing_exposures: list[str] = Field(default_factory=list)
    status: str
    run_id: str
