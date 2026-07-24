"""Reader for the official B3 COTAHIST fixed-width files."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any
import zipfile


class B3CotahistReader:
    """Parse only record type 01, spot market (TPMERC 010)."""

    layout_version = "COTAHIST-FW-v1"
    record_length = 245

    @staticmethod
    def _checksum(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _date(raw: str) -> date:
        return datetime.strptime(raw, "%Y%m%d").date()

    @staticmethod
    def _number(raw: str, scale: int = 100) -> float:
        value = raw.strip()
        return int(value or "0") / scale

    @staticmethod
    def _generation_date(header: str) -> date | None:
        for value in re.findall(r"(?:19|20)\d{6}", header):
            try:
                return datetime.strptime(value, "%Y%m%d").date()
            except ValueError:
                continue
        return None

    def read_text(
        self,
        text_path: Path,
        *,
        ticker: str,
        retrieved_at: datetime | None = None,
        pit_assurance: str = "RECONSTRUCTED_OFFICIAL_BACKFILL",
    ) -> list[dict[str, Any]]:
        payload = text_path.read_bytes()
        return self._parse(
            payload.decode("latin-1"), ticker=ticker, source_file=text_path.name,
            source_checksum=self._checksum(payload), retrieved_at=retrieved_at,
            pit_assurance=pit_assurance,
        )

    def read_zip(
        self,
        zip_path: Path,
        *,
        ticker: str,
        retrieved_at: datetime | None = None,
        pit_assurance: str = "RECONSTRUCTED_OFFICIAL_BACKFILL",
    ) -> list[dict[str, Any]]:
        zip_payload = zip_path.read_bytes()
        zip_checksum = self._checksum(zip_payload)
        rows: list[dict[str, Any]] = []
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".txt", ".csv")):
                    text = archive.read(name).decode("latin-1")
                    rows.extend(self._parse(
                        text, ticker=ticker, source_file=name,
                        source_checksum=self._checksum(text.encode("latin-1")),
                        retrieved_at=retrieved_at, pit_assurance=pit_assurance,
                        source_zip_checksum=zip_checksum,
                    ))
        return rows

    def _parse(
        self,
        text: str,
        *,
        ticker: str,
        source_file: str,
        source_checksum: str,
        retrieved_at: datetime | None,
        pit_assurance: str,
        source_zip_checksum: str | None = None,
    ) -> list[dict[str, Any]]:
        lines = text.splitlines()
        generated = next((self._generation_date(line) for line in lines if line[:2] == "00"), None)
        result: dict[tuple[str, date, str], dict[str, Any]] = {}
        for line in lines:
            if line[:2] != "01":
                continue
            if len(line) != self.record_length:
                continue
            if line[12:24].strip() != ticker:
                continue
            if line[24:27] != "010":
                continue
            reference_currency_raw = line[52:56].strip()
            currency_map = {"BRL": "BRL", "R$": "BRL"}
            currency = currency_map.get(reference_currency_raw)
            if currency is None:
                continue
            try:
                trade_date = self._date(line[2:10])
            except ValueError:
                continue
            quote_factor = int(line[210:217].strip() or "1")
            if quote_factor <= 0:
                continue
            raw_quoted_price = self._number(line[108:121])
            close_price = raw_quoted_price / quote_factor
            if close_price <= 0:
                continue
            raw = line.encode("latin-1")
            record = {
                "ticker": ticker,
                "trade_date": datetime.combine(trade_date, datetime.min.time(), tzinfo=timezone.utc),
                "close_price": close_price,
                "currency": currency,
                "reference_currency_raw": reference_currency_raw,
                "market_type": "010",
                "quote_factor": quote_factor,
                "raw_quoted_price": raw_quoted_price,
                "normalized_unit_price": close_price,
                "isin": line[230:242].strip() or None,
                "source_file": source_file,
                "source_checksum": source_checksum,
                "source_zip_checksum": source_zip_checksum,
                "file_generation_date": generated,
                "layout_version": self.layout_version,
                "record_hash": hashlib.sha256(raw).hexdigest(),
                "retrieved_at": retrieved_at or datetime.now(timezone.utc),
                "pit_assurance": pit_assurance,
            }
            result[(ticker, trade_date, "010")] = record
        return list(result.values())
