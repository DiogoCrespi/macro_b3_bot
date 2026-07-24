"""Sprint 4F domain models for Research Timing, Risk & Invalidation."""

from hashlib import sha256
import json
from typing import Any, Literal
from pydantic import BaseModel, Field


class ResearchTimingRiskSnapshot(BaseModel):
    """
    Audit-ready, content-addressed snapshot evaluating timing, event freshness,
    pricing risk, volatility/liquidity state, thesis invalidators, and review triggers.
    """
    timing_risk_id: str
    ticker: str
    as_of_timestamp: str
    research_decision_id: str
    catalysts: list[dict[str, Any]] = Field(default_factory=list)
    expected_horizon: dict[str, Any] = Field(default_factory=dict)
    event_freshness: dict[str, Any] = Field(default_factory=dict)
    pricing_risk: dict[str, Any] = Field(default_factory=dict)
    volatility_state: dict[str, Any] = Field(default_factory=dict)
    liquidity_state: dict[str, Any] = Field(default_factory=dict)
    thesis_invalidators: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    review_triggers: list[str] = Field(default_factory=list)
    next_review_at: str = ""
    timing_classification: Literal["MONITOR", "WAIT_FOR_CONFIRMATION", "AVOID"] = "MONITOR"
    risk_classification: Literal["LOW_RISK", "MODERATE_RISK", "ELEVATED_RISK", "HIGH_RISK", "UNACCEPTABLE_RISK"] = "MODERATE_RISK"
    confidence: float = 0.0
    methodology_version: str = "4F.1-research-timing-risk-v1"
    input_ids: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def compute_timing_risk_id(cls, payload: dict[str, Any]) -> str:
        """Computes a deterministic SHA256 content hash for the timing/risk payload."""
        canonical = {k: v for k, v in payload.items() if k != "timing_risk_id"}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()
