"""
Causal Graph Domain Models — Sprint 4B.

Defines nodes, edges, path propagation models, and SectorImpactCandidates.
BUY signals and stock ticker selections remain 100% disabled.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


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
    rationale: str


class SectorImpactCandidate(BaseModel):
    """
    Evaluated impact of a MacroEvent on a specific B3 sector or subsector.
    Does NOT select individual stock tickers. BUY signals remain permanently disabled.
    """
    candidate_id: str
    event_id: str
    event_type: str
    sector: str
    subsector: Optional[str] = None

    direction: str          # "BULLISH" or "BEARISH"
    impact_score: float     # tanh(sum(pathImpact))
    confidence: float       # weighted average path confidence
    horizon_months: int = 3

    causal_paths: list[list[str]] = Field(default_factory=list)
    direct_effects: list[str] = Field(default_factory=list)
    second_order_effects: list[str] = Field(default_factory=list)
    positive_paths_count: int = 0
    negative_paths_count: int = 0
    conflict_detected: bool = False
    invalidators: list[str] = Field(default_factory=list)

    status: str = SectorImpactStatus.SECTOR_IMPACT_WATCH.value
    detected_at: datetime
