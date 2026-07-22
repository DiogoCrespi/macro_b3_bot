from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field, model_validator


class AssetClass(StrEnum):
    STOCK = "stock"
    FII = "fii"
    ETF = "etf"
    BDR = "bdr"
    CASH = "cash"


class DecisionAction(StrEnum):
    BUY = "buy"
    WATCH = "watch"
    HOLD = "hold"
    REDUCE = "reduce"
    SELL = "sell"
    NO_ACTION = "no_action"


class Evidence(BaseModel):
    evidence_id: str
    source_id: str
    source_tier: int = Field(ge=1, le=3)
    claim: str
    observed_at: datetime
    published_at: datetime | None = None
    effective_date: datetime | None = None
    url: str | None = None
    confidence: float = Field(ge=0, le=1)
    entities: list[str] = Field(default_factory=list)
    raw_checksum: str | None = None


class MacroEvent(BaseModel):
    event_id: str
    title: str
    event_type: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    novelty_score: float = Field(ge=0, le=1)
    magnitude_score: float = Field(ge=0, le=1)
    persistence_score: float = Field(ge=0, le=1)
    evidence: list[Evidence]
    variables: dict[str, float | str] = Field(default_factory=dict)


class Scenario(BaseModel):
    scenario_id: str
    name: str
    probability: float = Field(ge=0, le=1)
    horizon_months: int = Field(gt=0)
    assumptions: list[str]
    expected_effects: dict[str, float] = Field(default_factory=dict)
    invalidators: list[str] = Field(default_factory=list)
    calibrated: bool = False


class ScenarioSet(BaseModel):
    event_id: str
    scenarios: list[Scenario]
    generated_by: str

    @model_validator(mode="after")
    def probabilities_are_reasonable(self) -> "ScenarioSet":
        total = sum(item.probability for item in self.scenarios)
        if self.scenarios and not 0.95 <= total <= 1.05:
            raise ValueError(f"scenario probabilities must sum close to 1.0; got {total:.4f}")
        return self


class AssetSnapshot(BaseModel):
    ticker: str
    asset_class: AssetClass
    as_of: datetime
    price: float = Field(gt=0)
    avg_daily_volume_brl: float = Field(ge=0)
    sector: str | None = None
    metrics: dict[str, float | None] = Field(default_factory=dict)
    source_fields: dict[str, str] = Field(default_factory=dict)


class OpportunityAssessment(BaseModel):
    ticker: str
    asset_class: AssetClass
    event_id: str
    evidence_quality: float = Field(ge=0, le=1)
    scenario_probability: float = Field(ge=0, le=1)
    causal_strength: float = Field(ge=0, le=1)
    company_exposure: float = Field(ge=0, le=1)
    fundamental_quality: float = Field(ge=0, le=1)
    valuation_attractiveness: float = Field(ge=0, le=1)
    entry_timing: float = Field(ge=0, le=1)
    portfolio_fit: float = Field(ge=0, le=1)
    penalties: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    expected_upside: float | None = None
    expected_downside: float | None = None
    independent_evidence_count: int = Field(ge=0)
    has_primary_source: bool = False
    risk_veto: bool = False
    skeptic_veto: bool = False
    stale_critical_data: bool = False
    thesis: list[str] = Field(default_factory=list)
    invalidators: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def reward_risk(self) -> float | None:
        if self.expected_upside is None or self.expected_downside is None:
            return None
        downside = abs(self.expected_downside)
        return None if downside == 0 else self.expected_upside / downside


class InvestmentDecision(BaseModel):
    ticker: str
    action: DecisionAction
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    invalidators: list[str] = Field(default_factory=list)
    reward_risk: float | None = None
    max_position_pct: float = Field(default=0, ge=0, le=1)
    entry_range: tuple[float, float] | None = None
    horizon_months: int | None = None
