"""
Causal Graph Domain Models — Sprint 4B.

Defines nodes, edges, path propagation models, and SectorImpactCandidates.
BUY signals and stock ticker selections remain 100% disabled.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class CausalNodeType(str, Enum):
    MACRO_EVENT = "MACRO_EVENT"
    MACRO_VARIABLE = "MACRO_VARIABLE"
    MARKET_FACTOR = "MARKET_FACTOR"
    B3_SECTOR = "B3_SECTOR"


class SectorImpactStatus(str, Enum):
    SECTOR_IMPACT_APPROVED = "SECTOR_IMPACT_APPROVED"
    SECTOR_IMPACT_WATCH = "SECTOR_IMPACT_WATCH"
    SECTOR_IMPACT_REJECTED = "SECTOR_IMPACT_REJECTED"


class CausalEdge(BaseModel):
    """
    A directed edge in the deterministic causal graph.
    """
    edge_id: str
    source_node: str
    target_node: str

    direction: int          # -1 (inverse) or +1 (direct)
    strength: float         # 0.0 to 1.0
    confidence: float       # 0.0 to 1.0

    lag_days: int = 0
    horizon_days: int = 90
    half_life_days: int = 45

    regime_conditions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    hypothesis: bool = True
    factor: str | None = None
    company_channel_effects: dict[str, int] = Field(default_factory=dict)
    rationale: str

    @model_validator(mode="after")
    def evidence_or_hypothesis(self) -> "CausalEdge":
        if not self.evidence_ids and not self.hypothesis:
            raise ValueError("An edge without evidence_ids must be marked as a hypothesis")
        if any(
            channel not in {"revenue", "cost", "debt", "demand"}
            or direction not in {-1, 1}
            for channel, direction in self.company_channel_effects.items()
        ):
            raise ValueError("company channel effects must use known channels and +/-1")
        return self


class CausalPath(BaseModel):
    path_id: str
    nodes: list[str] = Field(min_length=2)
    causal_edge_ids: list[str] = Field(min_length=1)
    factor: str
    company_channel_effects: dict[str, int]
    factor_direction: int
    direction: int
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_status: str


class SectorImpactCandidate(BaseModel):
    """
    Evaluated impact of a MacroEvent on a specific B3 sector or subsector.
    Does NOT select individual stock tickers. BUY signals remain permanently disabled.
    """
    candidate_id: str
    event_id: str
    event_type: str
    causal_root: str
    sector: str
    subsector: Optional[str] = None

    direction: str          # "BULLISH" or "BEARISH"
    impact_score: float     # signed tanh(sum(pathImpact))
    event_strength: float
    confidence: float       # weighted average path confidence
    horizon_months: int = 3
    horizon_days: int = 90

    causal_paths: list[CausalPath] = Field(default_factory=list)
    direct_effects: list[str] = Field(default_factory=list)
    second_order_effects: list[str] = Field(default_factory=list)
    positive_paths_count: int = 0
    negative_paths_count: int = 0
    conflict_detected: bool = False
    invalidators: list[str] = Field(default_factory=list)
    evidence_status: str = "HYPOTHESIS"

    status: str = SectorImpactStatus.SECTOR_IMPACT_WATCH.value
    detected_at: datetime
    event_available_at: datetime
    as_of_timestamp: datetime
    run_id: str
    source_event_run_id: str
    graph_version: str


class SectorStateSnapshot(BaseModel):
    snapshot_id: str
    sector: str
    as_of_timestamp: datetime
    net_impact: float
    bullish_impact: float
    bearish_impact: float
    conflict_ratio: float
    supporting_event_ids: list[str] = Field(default_factory=list)
    opposing_event_ids: list[str] = Field(default_factory=list)
    confidence: float
    status: str
    run_id: str
    graph_version: str
