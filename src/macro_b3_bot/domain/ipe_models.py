from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field

class IpeDocumentIndex(BaseModel):
    document_id: str
    cvm_code: str
    company_name: str
    category: str
    document_type: str | None = None
    subject: str | None = None

    reference_date: date | None = None
    delivery_date: datetime
    protocol: str | None = None
    version: int = 1

    source_url: str | None = None
    raw_index_checksum: str
    record_checksum: str
    ingestion_run_id: str

class IpeProcessingState(BaseModel):
    document_id: str
    status: str = Field(pattern="^(DISCOVERED|QUEUED|DOWNLOADED|PARSED|EXTRACTED|DEDUPLICATED|EVIDENCE_BUILT|REJECTED|FAILED)$")
    priority_score: float = Field(ge=0.0, le=1.0)
    category_score: float = Field(default=0.0, ge=0.0, le=1.0)
    recency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ticker_mapping_score: float = Field(default=0.0, ge=0.0, le=1.0)
    liquidity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    material_terms_score: float = Field(default=0.0, ge=0.0, le=1.0)
    materiality_score: float | None = None
    attempts: int = 0
    last_error: str | None = None
    updated_at: datetime
