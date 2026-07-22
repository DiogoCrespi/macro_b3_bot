from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

class DownloadedDocument(BaseModel):
    document_id: str
    source_url: str
    http_status: int
    mime_type: str
    file_extension: str | None = None
    file_size_bytes: int = Field(ge=0)
    raw_path: str
    document_checksum: str
    downloaded_at: datetime
    ingestion_run_id: str

class ExtractedDocument(BaseModel):
    document_id: str
    document_checksum: str
    extraction_method: str
    text: str
    text_length: int
    page_count: int | None = None
    language: str | None = None
    normalized_text_checksum: str
    extraction_quality: float = Field(ge=0.0, le=1.0)
    extracted_at: datetime
