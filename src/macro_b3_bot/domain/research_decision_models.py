"""Sprint 4E.3 domain models for Research Decision Synthesis."""

from hashlib import sha256
import json
from typing import Any, Literal
from pydantic import BaseModel, Field


class ResearchDecisionSnapshot(BaseModel):
    """
    Audit-ready, content-addressed decision snapshot synthesizing macro events,
    sector state, company exposures, financial bridges, calibration assessments,
    and historical valuation context.
    """
    decision_id: str
    ticker: str
    as_of_timestamp: str
    decision: Literal["WATCH", "NO_ACTION"]
    macro_event_ids: list[str] = Field(default_factory=list)
    active_factors: list[str] = Field(default_factory=list)
    factor_directions: dict[str, int | None] = Field(default_factory=dict)
    macro_conflict_status: str = "NO_CONFLICT"
    sector_state: dict[str, Any] | str | None = None
    sector_impact: dict[str, Any] | str | None = None
    company_contributions: list[dict[str, Any]] = Field(default_factory=list)
    financial_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    historical_multiple_position: dict[str, Any] | None = None
    price_implied_fundamentals: dict[str, Any] | None = None
    valuation_classification: str = "VALUATION_BLOCKED"
    evidence_completeness: float = 0.0
    confidence: float = 0.0
    confidence_tier: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    critical_blockers: list[str] = Field(default_factory=list)
    noncritical_warnings: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    rationale: str = ""
    invalidation_conditions: list[str] = Field(default_factory=list)
    methodology_version: str = "4E.3-research-decision-synthesis-v1"
    execution_mode: str = "BLOCKED_MISSING_UPSTREAM_INPUT"
    input_ids: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def compute_decision_id(cls, payload: dict[str, Any]) -> str:
        """Computes a deterministic SHA256 content hash for the complete decision payload."""
        canonical = {k: v for k, v in payload.items() if k != "decision_id"}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()
