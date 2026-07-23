"""Targeted official FRE XML/PDF ingestion for the company exposure pilot."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import httpx
import pandas as pd

from macro_b3_bot.adapters.cvm.extractors.pdf_extractor import PdfExtractor
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


_FRE_SECTIONS = {
    "InfoSegmentosOperacionais",
    "ProducaoComercializacaoMercados",
    "CondicoesFinanceirasPatrimoniais",
    "ResultadosOperFinanceiros",
    "DescricaoRiscosMercado",
}


class CompanyFreDocumentPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"
        self.raw_dir = settings.data_dir / "raw" / "cvm" / "fre"
        self.pdf_extractor = PdfExtractor()

    def ingest(self, as_of_timestamp: datetime) -> dict[str, object]:
        cutoff = as_of_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        store = DatabaseStore(self.db_path)
        self._ensure_table(store)
        selected = self._select_latest(store, cutoff)
        results = []
        with httpx.Client(
            follow_redirects=True,
            timeout=120,
            headers={"User-Agent": "Mozilla/5.0 macro-b3-research/1.0"},
        ) as client:
            for item in selected:
                try:
                    results.append(self._ingest_one(store, client, item))
                except Exception as exc:
                    results.append({
                        "ticker": item["ticker"], "document_id": item["document_id"],
                        "status": "FAILED", "error": str(exc)[:300],
                    })
        store.close()
        return {
            "as_of_timestamp": as_of_timestamp.astimezone(timezone.utc).isoformat(),
            "companies_selected": len(selected),
            "documents_ingested": sum(item["status"] == "INGESTED" for item in results),
            "failures": sum(item["status"] == "FAILED" for item in results),
            "sections_extracted": sum(item.get("sections", 0) for item in results),
            "results": results,
        }

    def _select_latest(
        self, store: DatabaseStore, cutoff: datetime
    ) -> list[dict[str, object]]:
        mappings = store.connection.execute(
            """
            SELECT ticker,CAST(cvm_code AS INTEGER)
            FROM company_ticker_map WHERE review_status='VALIDATED'
            """
        ).fetchall()
        ticker_by_code = {int(code): ticker for ticker, code in mappings}
        rows = []
        for year in (2025, 2026):
            path = self.settings.data_dir / "raw" / "cvm" / "statements" / (
                f"fre_cia_aberta_{year}.zip"
            )
            if not path.exists():
                continue
            with ZipFile(path) as archive:
                frame = pd.read_csv(
                    archive.open(f"fre_cia_aberta_{year}.csv"),
                    sep=";", encoding="latin1", low_memory=False,
                )
            frame["DT_RECEB"] = pd.to_datetime(frame["DT_RECEB"])
            frame = frame[
                frame["CD_CVM"].astype(int).isin(ticker_by_code)
                & (frame["DT_RECEB"] <= cutoff)
            ]
            for record in frame.to_dict("records"):
                rows.append({
                    "ticker": ticker_by_code[int(record["CD_CVM"])],
                    "cvm_code": str(int(record["CD_CVM"])),
                    "document_id": str(int(record["ID_DOC"])),
                    "version": int(record["VERSAO"]),
                    "reference_date": str(record["DT_REFER"]),
                    "available_at": record["DT_RECEB"].to_pydatetime(),
                    "source_url": str(record["LINK_DOC"]).replace(
                        "http://www.rad.cvm.gov.br", "https://www.rad.cvm.gov.br"
                    ),
                })
        latest: dict[str, dict[str, object]] = {}
        for item in sorted(
            rows, key=lambda row: (row["available_at"], row["version"])
        ):
            latest[item["ticker"]] = item
        return [latest[ticker] for ticker in sorted(latest)]

    def _ingest_one(
        self, store: DatabaseStore, client: httpx.Client, item: dict[str, object]
    ) -> dict[str, object]:
        document_dir = (
            self.raw_dir / str(item["cvm_code"]) / str(item["document_id"])
        )
        document_dir.mkdir(parents=True, exist_ok=True)
        cached = sorted(document_dir.glob("*.zip"))
        if cached:
            payload = cached[-1].read_bytes()
        else:
            response = client.get(str(item["source_url"]))
            response.raise_for_status()
            payload = response.content
        checksum = hashlib.sha256(payload).hexdigest()
        raw_path = document_dir / f"{checksum[:16]}.zip"
        if not raw_path.exists():
            raw_path.write_bytes(payload)
        with ZipFile(io.BytesIO(payload)) as archive:
            xml_name = next(
                name for name in archive.namelist()
                if "FRE" in name.upper() and name.lower().endswith(".xml")
            )
            xml_bytes = archive.read(xml_name)
        xml_text = xml_bytes.decode("cp1252").replace(
            'encoding="utf-8"', 'encoding="windows-1252"'
        )
        root = ET.fromstring(xml_text)
        extracted = 0
        for element in root.iter():
            if element.tag not in _FRE_SECTIONS:
                continue
            encoded = element.findtext(".//ImagemObjetoArquivoPdf")
            source_name = element.findtext(".//NomeArquivoPdf")
            if not encoded:
                continue
            pdf_bytes = base64.b64decode(encoded)
            page_texts = self.pdf_extractor.extract_pages(pdf_bytes)
            text = " ".join(page_texts)
            pages = len(page_texts)
            quality = 0.85 if len(text) >= 100 else 0.20
            section_checksum = hashlib.sha256(pdf_bytes).hexdigest()
            store.connection.execute(
                """
                INSERT OR REPLACE INTO company_fre_sections (
                    ticker,cvm_code,document_id,version,reference_date,available_at,
                    source_url,raw_document_checksum,raw_path,section_name,
                    source_filename,section_checksum,extracted_text,page_count,
                    extracted_pages,extraction_quality,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    item["ticker"], item["cvm_code"], item["document_id"],
                    item["version"], item["reference_date"], item["available_at"],
                    item["source_url"], checksum, str(raw_path), element.tag,
                    source_name, section_checksum, text, pages,
                    json.dumps(page_texts, ensure_ascii=False), quality,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ],
            )
            extracted += 1
        return {
            "ticker": item["ticker"], "document_id": item["document_id"],
            "version": item["version"], "available_at": item["available_at"],
            "raw_checksum": checksum, "sections": extracted, "status": "INGESTED",
        }

    @staticmethod
    def _ensure_table(store: DatabaseStore) -> None:
        store.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS company_fre_sections (
                ticker VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                document_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                reference_date DATE NOT NULL,
                available_at TIMESTAMP NOT NULL,
                source_url VARCHAR NOT NULL,
                raw_document_checksum VARCHAR NOT NULL,
                raw_path VARCHAR NOT NULL,
                section_name VARCHAR NOT NULL,
                source_filename VARCHAR,
                section_checksum VARCHAR NOT NULL,
                extracted_text VARCHAR NOT NULL,
                page_count INTEGER,
                extracted_pages VARCHAR,
                extraction_quality DOUBLE NOT NULL,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY(document_id,version,section_name,section_checksum)
            )
            """
        )
        columns = {
            row[1] for row in store.connection.execute(
                "PRAGMA table_info('company_fre_sections')"
            ).fetchall()
        }
        if "extracted_pages" not in columns:
            store.connection.execute(
                "ALTER TABLE company_fre_sections ADD COLUMN extracted_pages VARCHAR"
            )
