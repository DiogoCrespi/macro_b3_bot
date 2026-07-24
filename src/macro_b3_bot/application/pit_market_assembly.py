"""Assemble validated B3/CVM records into PIT market snapshots."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any

from pydantic import BaseModel, model_validator

from macro_b3_bot.application.market_snapshot_pilot import PITMarketDataIngestor
from macro_b3_bot.domain.financial_bridge_models import MarketSnapshotPIT


class PITSecurityMapping(BaseModel):
    ticker: str
    cvm_code: str
    cnpj: str
    isin: str
    security_type: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    mapping_source: str
    mapping_available_at: datetime
    mapping_checksum: str
    source_file: str
    source_file_checksum: str
    source_record_hash: str
    source_locator: str
    effective_date_source: str = "UNKNOWN"

    @model_validator(mode="after")
    def valid_interval(self) -> "PITSecurityMapping":
        if self.valid_from and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("mapping validity interval is inverted")
        return self

    def is_valid_at(self, as_of: datetime) -> bool:
        return (self.valid_from is None or self.valid_from <= as_of) and (
            self.valid_to is None or as_of <= self.valid_to
        ) and self.mapping_available_at <= as_of

    @property
    def mapping_id(self) -> str:
        payload = self.model_dump(mode="json")
        return "map-" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]


class PITMarketSnapshotAssembler:
    """Reconcile security identity before handing records to the frozen ingestor."""

    def __init__(self, ingestor: PITMarketDataIngestor | None = None) -> None:
        self.ingestor = ingestor or PITMarketDataIngestor()

    def assemble(
        self,
        *,
        mapping: PITSecurityMapping,
        assessment_as_of: datetime,
        price_record: dict[str, Any],
        share_record: dict[str, Any],
        unit_composition: list[str] | None = None,
    ) -> MarketSnapshotPIT:
        if not mapping.is_valid_at(assessment_as_of):
            raise ValueError(f"{mapping.ticker}: PIT security mapping is not valid at assessment")
        if price_record.get("ticker") != mapping.ticker:
            raise ValueError(f"{mapping.ticker}: B3 ticker does not match mapping")
        if price_record.get("isin") != mapping.isin:
            raise ValueError(f"{mapping.ticker}: B3 ISIN does not match mapping")
        company_cnpj = share_record.get("company_cnpj")
        if not company_cnpj:
            raise ValueError(f"{mapping.ticker}: CVM CNPJ is required")
        normalized = "".join(c for c in str(company_cnpj) if c.isdigit())
        expected = "".join(c for c in mapping.cnpj if c.isdigit())
        if normalized != expected:
            raise ValueError(f"{mapping.ticker}: CVM CNPJ does not match mapping")
        if str(share_record.get("cvm_code") or "") != mapping.cvm_code:
            raise ValueError(f"{mapping.ticker}: CVM code is required and must match mapping")
        available = price_record.get("available_at") or price_record.get("retrieved_at")
        if available is None:
            raise ValueError(f"{mapping.ticker}: explicit price availability is required")
        price_record = {**price_record, "available_at": available}
        share_record = {
            **share_record,
            "share_count_available_at": share_record.get("document_available_at"),
        }
        basis = "UNITS_OUTSTANDING" if mapping.security_type == "UNIT" else "SHARES_OUTSTANDING"
        equity_basis = "UNIT_PRICE_X_UNITS" if mapping.security_type == "UNIT" else "PRICE_X_SHARES"
        return self.ingestor.build_snapshot(
            ticker=mapping.ticker,
            assessment_as_of=assessment_as_of,
            price_record=price_record,
            share_record=share_record,
            security_type=mapping.security_type,
            equity_value_basis=equity_basis,
            share_count_basis=basis,
            unit_composition=unit_composition,
        )
