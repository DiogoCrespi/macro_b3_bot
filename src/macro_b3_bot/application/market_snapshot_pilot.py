"""Sprint 4E.1C ingestion boundary for official PIT market records.

Network retrieval is intentionally outside this module.  Callers provide the
downloaded B3 close record and the CVM capital-structure record, preserving
their checksums and availability timestamps verbatim.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from macro_b3_bot.domain.financial_bridge_models import MarketSnapshotPIT
from macro_b3_bot.infrastructure.store import DatabaseStore


def _timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PITMarketDataIngestor:
    """Build and persist one auditable price/share-count snapshot."""

    def build_snapshot(
        self,
        *,
        ticker: str,
        assessment_as_of: datetime,
        price_record: dict[str, Any],
        share_record: dict[str, Any],
        security_type: str,
        equity_value_basis: str,
        share_count_basis: str,
        unit_composition: list[str] | None = None,
    ) -> MarketSnapshotPIT:
        if price_record.get("close_price") is None:
            raise ValueError(f"{ticker}: B3 close_price is required")
        share_count = share_record.get("outstanding_count", share_record.get("share_count"))
        share_as_of = share_record.get("share_count_as_of", share_record.get("as_of", share_record.get("capital_reference_date")))
        share_available_at = share_record.get("share_count_available_at", share_record.get("available_at", share_record.get("document_available_at")))
        if share_count is None:
            raise ValueError(f"{ticker}: official share_count is required")
        if share_as_of is None or share_available_at is None:
            raise ValueError(f"{ticker}: share-count PIT dates are required")
        source_file = str(price_record.get("source_file") or "")
        source_checksum = str(price_record.get("source_checksum") or "")
        if not source_file or not source_checksum:
            raise ValueError(f"{ticker}: B3 source file and checksum are required")
        document_id = str(share_record.get("document_id") or "")
        document_checksum = str(share_record.get("document_checksum") or "")
        if not document_id or not document_checksum:
            raise ValueError(f"{ticker}: CVM document ID and checksum are required")
        return MarketSnapshotPIT.from_content(
            ticker=ticker,
            assessment_as_of=assessment_as_of,
            price_as_of=_timestamp(price_record["trade_date"]),
            price_available_at=_timestamp(price_record["available_at"]),
            share_count_as_of=_timestamp(share_as_of),
            share_count_available_at=_timestamp(share_available_at),
            price=float(price_record["close_price"]),
            share_count=float(share_count),
            share_count_basis=share_count_basis,
            currency=str(price_record.get("currency") or "BRL"),
            source_id=f"B3:{source_file}",
            market_data_version=str(price_record.get("layout_version") or "unknown"),
            security_type=security_type,
            equity_value_basis=equity_value_basis,
            unit_composition=unit_composition or [],
            price_source_file=source_file,
            price_source_checksum=source_checksum,
            price_layout_version=price_record.get("layout_version"),
            price_record_hash=price_record.get("record_hash"),
            share_document_id=document_id,
            share_document_version=(
                str(share_record["document_version"])
                if share_record.get("document_version") is not None else None
            ),
            share_document_checksum=document_checksum,
            share_section=share_record.get("section"),
        )

    @staticmethod
    def persist(store: DatabaseStore, snapshot: MarketSnapshotPIT) -> None:
        store.save_market_snapshot_pit(snapshot.model_dump(mode="json"))
