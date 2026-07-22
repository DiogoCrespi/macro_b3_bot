from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

class EventCandidate(BaseModel):
    event_id: str
    ticker: str
    cvm_code: str

    event_type: Literal[
        "DIVIDEND_DECLARED",
        "JCP_DECLARED",
        "BUYBACK_AUTHORIZED",
        "CAPITAL_INCREASE",
        "ACQUISITION",
        "DIVESTMENT",
        "DEBT_ISSUANCE",
        "DEBT_RENEGOTIATION",
        "GUIDANCE_CHANGED",
        "RECOVERY_EVENT",
        "OPERATIONAL_INTERRUPTION"
    ]
    title: str
    effective_date: Optional[date] = None

    claim_ids: List[str] = Field(default_factory=list)
    evidence_count: int = 1

    novelty_score: float = Field(ge=0.0, le=1.0)
    materiality_score: float = Field(ge=0.0, le=1.0)
    persistence_score: float = Field(default=0.8, ge=0.0, le=1.0)

    quantitative_impact: Dict[str, Decimal] = Field(default_factory=dict)
    invalidators: List[str] = Field(default_factory=list)

    status: Literal[
        "CANDIDATE",
        "REJECTED",
        "EVENT_GATE_APPROVED"
    ] = "CANDIDATE"
    created_at: datetime
