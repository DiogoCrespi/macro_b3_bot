from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field

class EvidenceClaim(BaseModel):
    claim_id: str
    document_id: str
    cvm_code: str
    ticker: str | None = None

    claim_type: str # ex: DIVIDEND, JCP, SHARE_BUYBACK, CAPITAL_INCREASE, DEBT_ISSUANCE
    subject: str
    predicate: str
    object_text: str

    numeric_value: Decimal | None = None
    unit: str | None = None
    currency: str | None = None

    effective_date: date | None = None
    horizon_end: date | None = None

    source_page: int | None = None
    source_start: int | None = None
    source_end: int | None = None
    source_excerpt: str

    extraction_method: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime
