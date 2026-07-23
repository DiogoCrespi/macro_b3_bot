from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel

class CvmCompany(BaseModel):
    cvm_code: str
    cnpj: str
    legal_name: str
    trading_name: str | None = None
    registration_status: str
    registration_date: date | None = None
    cancellation_date: date | None = None
    category: str | None = None
    collected_at: datetime
    record_checksum: str
    ingestion_run_id: str

class CvmDocument(BaseModel):
    document_id: str
    document_type: str
    cvm_code: str
    cnpj: str
    reference_date: date
    received_at: datetime
    filing_available_at: datetime | None = None
    resource_last_modified_at: datetime | None = None
    collected_at: datetime | None = None
    availability_precision: str = "UNKNOWN"
    version: int
    raw_zip_checksum: str
    ingestion_run_id: str
    availability_basis: str | None = None
    source_url: str | None = None

class FinancialStatementLine(BaseModel):
    document_id: str
    statement_type: str
    scope: str
    fiscal_order: str
    account_code: str
    account_description: str
    value: Decimal
    currency: str
    scale: int
    start_date: date | None = None
    end_date: date
    record_checksum: str
