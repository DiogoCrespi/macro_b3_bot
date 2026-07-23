"""Point-in-time source packet for the 15-company exposure pilot."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from enum import StrEnum
import re
from uuid import uuid4

from macro_b3_bot.adapters.cvm.ipe_document_client import IpeDocumentDownloader
from macro_b3_bot.application.extract_ipe_documents import IpeExtractionPipeline
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


class ExposureDocumentClass(StrEnum):
    EARNINGS_RELEASE = "EARNINGS_RELEASE"
    RESULTS_PRESENTATION = "RESULTS_PRESENTATION"
    ANNUAL_REPORT = "ANNUAL_REPORT"
    REFERENCE_FORM = "REFERENCE_FORM"
    DEBT_DISCLOSURE = "DEBT_DISCLOSURE"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    ESG = "ESG"
    OTHER = "OTHER"


_NEGATIVE_SUBJECT_TERMS = (
    "leilão de frações", "leilao de fracoes", "assembleia", "eleição de administrador",
    "eleicao de administrador", "dividendos", "juros sobre capital", "aumento de capital",
    "recompra de ações", "recompra de acoes", "boletim de voto", "oferta pública",
    "oferta publica", "alocação da oferta", "alocacao da oferta", "direito de preferência",
    "direito de preferencia", "incorporação", "incorporacao", "reorganização societária",
)
_RESULT_TERMS = (
    "resultado 1t", "resultados 1t", "resultado 2t", "resultados 2t",
    "resultado 3t", "resultados 3t", "resultado 4t", "resultados 4t",
    "desempenho 1t", "desempenho 2t", "desempenho 3t", "desempenho 4t",
    "earnings release", "release de resultados", "divulgação de resultados",
    "divulgacao de resultados",
)
_DEBT_TERMS = (
    "endividamento", "dívida", "divida", "debênture", "debenture",
    "financiamento", "bonds", "liquidez",
)


def classify_exposure_document(
    category: str | None, document_type: str | None, subject: str | None
) -> ExposureDocumentClass:
    """Classify by the filing's declared metadata, not a bare 'resultado' substring."""
    category_text = (category or "").casefold()
    type_text = (document_type or "").casefold()
    subject_text = (subject or "").casefold()
    combined = f"{category_text} {type_text} {subject_text}"
    if any(term in subject_text for term in _NEGATIVE_SUBJECT_TERMS):
        return ExposureDocumentClass.CORPORATE_ACTION
    if "esg" in combined or "sustentabilidade" in combined:
        return ExposureDocumentClass.ESG
    if "formulário de referência" in combined or "formulario de referencia" in combined:
        return ExposureDocumentClass.REFERENCE_FORM
    if "relatório anual" in combined or "relatorio anual" in combined or "20-f" in combined:
        return ExposureDocumentClass.ANNUAL_REPORT
    if any(term in combined for term in _DEBT_TERMS):
        return ExposureDocumentClass.DEBT_DISCLOSURE
    has_financial_result = any(term in subject_text for term in _RESULT_TERMS) or bool(
        re.search(r"(?:resultado|resultados|desempenho).{0,24}[1-4]t\d{2}", subject_text)
    )
    if has_financial_result and (
        "press-release" in type_text or "dados econômico-financeiros" in category_text
    ):
        return ExposureDocumentClass.EARNINGS_RELEASE
    if has_financial_result and "apresenta" in type_text:
        return ExposureDocumentClass.RESULTS_PRESENTATION
    return ExposureDocumentClass.OTHER


_SELECTABLE_CLASSES = {
    ExposureDocumentClass.EARNINGS_RELEASE,
    ExposureDocumentClass.RESULTS_PRESENTATION,
    ExposureDocumentClass.ANNUAL_REPORT,
    ExposureDocumentClass.REFERENCE_FORM,
    ExposureDocumentClass.DEBT_DISCLOSURE,
}


class CompanyExposureDocumentPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"
        self.downloader = IpeDocumentDownloader(
            settings.data_dir / "raw" / "cvm" / "ipe"
        )

    async def ingest(
        self, as_of_timestamp: datetime, documents_per_family: int = 1
    ) -> dict[str, object]:
        run_id = f"RUN_COMPANY_EXPOSURE_DOCS_{uuid4().hex[:8]}"
        cutoff = as_of_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        store = DatabaseStore(self.db_path)
        self._ensure_selection_table(store)
        candidates = store.connection.execute(
            """
            SELECT m.ticker,i.document_id,i.source_url,i.cvm_code,
                   EXTRACT(YEAR FROM i.delivery_date) AS year,
                   i.version,i.category,i.document_type,i.subject,i.delivery_date
            FROM company_ticker_map m
            JOIN ipe_document_index i ON i.cvm_code=m.cvm_code
            WHERE m.review_status='VALIDATED'
              AND i.delivery_date <= ?
              AND i.source_url IS NOT NULL AND i.source_url <> ''
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY m.ticker,COALESCE(i.protocol,i.document_id)
                ORDER BY i.version DESC,i.delivery_date DESC,i.document_id DESC
            ) = 1
            ORDER BY m.ticker,i.delivery_date DESC,i.version DESC
            """,
            [cutoff],
        ).fetchall()
        selected_rows: list[tuple] = []
        family_counts: dict[tuple[str, str], int] = defaultdict(int)
        for row in candidates:
            ticker, _, _, _, _, _, category, document_type, subject, _ = row
            classification = classify_exposure_document(category, document_type, subject)
            key = (ticker, classification.value)
            if (
                classification in _SELECTABLE_CLASSES
                and family_counts[key] < documents_per_family
            ):
                selected_rows.append((*row, classification.value))
                family_counts[key] += 1

        downloaded = 0
        already_available = 0
        failures: list[str] = []
        selected_ids: list[str] = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for (
            ticker, document_id, source_url, cvm_code, year, version, category,
            document_type, subject, delivery_date, classification,
        ) in selected_rows:
            selected_ids.append(document_id)
            existing = store.connection.execute(
                """
                SELECT document_checksum FROM downloaded_documents
                WHERE document_id=? AND source_url=?
                ORDER BY downloaded_at DESC LIMIT 1
                """,
                [document_id, source_url],
            ).fetchone()
            checksum = existing[0] if existing else None
            if existing:
                already_available += 1
            else:
                result = await self.downloader.download_document(
                    document_id=document_id, source_url=source_url,
                    cvm_code=cvm_code, year=int(year), ingestion_run_id=run_id,
                )
                if result is None:
                    failures.append(f"{ticker}:{document_id}:DOWNLOAD_FAILED")
                    self._save_selection(
                        store, run_id, ticker, document_id, version, subject,
                        document_type, delivery_date, source_url, classification,
                        "family_latest_point_in_time", None, "DOWNLOAD_FAILED",
                    )
                    continue
                store.save_downloaded_document(result.model_dump(mode="json"))
                checksum = result.document_checksum
                downloaded += 1
            self._save_selection(
                store, run_id, ticker, document_id, version, subject, document_type,
                delivery_date, source_url, classification,
                "family_latest_point_in_time", checksum, "DOWNLOADED",
            )
            store.connection.execute(
                """
                INSERT INTO ipe_processing_queue (
                    document_id,status,priority_score,category_score,recency_score,
                    ticker_mapping_score,liquidity_score,material_terms_score,
                    attempts,created_at,updated_at
                ) VALUES (?, 'DOWNLOADED', 1,1,1,1,1,1,0,?,?)
                ON CONFLICT(document_id) DO UPDATE SET
                    status=CASE
                      WHEN ipe_processing_queue.status IN
                           ('EXTRACTED','DEDUPLICATED','EVIDENCE_BUILT')
                      THEN ipe_processing_queue.status ELSE 'DOWNLOADED' END,
                    updated_at=excluded.updated_at
                """,
                [document_id, now, now],
            )
        store.close()
        extraction = IpeExtractionPipeline(self.settings).extract_downloaded_batch(
            limit=max(1, len(selected_ids)), document_ids=selected_ids
        )
        coverage = self.coverage(run_id)
        return {
            "run_id": run_id,
            "selected": len(selected_rows),
            "downloaded": downloaded,
            "already_available": already_available,
            "failures": failures,
            **extraction,
            "coverage": coverage,
        }

    def coverage(self, run_id: str) -> list[dict[str, object]]:
        store = DatabaseStore(self.db_path)
        rows = store.connection.execute(
            """
            SELECT s.ticker,
                   COUNT(*) AS selected,
                   COUNT(s.document_checksum) AS downloaded,
                   COUNT(e.document_id) AS extracted,
                   COUNT(*) FILTER (
                     WHERE s.classification NOT IN ('CORPORATE_ACTION','ESG','OTHER')
                   ) AS relevant,
                   COUNT(*) FILTER (WHERE s.extraction_status LIKE '%FAILED') AS failures
            FROM company_exposure_document_selections s
            LEFT JOIN extracted_documents e
              ON e.document_id=s.document_id
             AND e.document_checksum=s.document_checksum
            WHERE s.selection_run_id=?
            GROUP BY s.ticker ORDER BY s.ticker
            """,
            [run_id],
        ).fetchall()
        store.close()
        return [
            {
                "ticker": row[0], "selected": row[1], "downloaded": row[2],
                "extracted": row[3], "relevant": row[4], "failures": row[5],
            }
            for row in rows
        ]

    @staticmethod
    def _ensure_selection_table(store: DatabaseStore) -> None:
        store.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS company_exposure_document_selections (
                selection_run_id VARCHAR NOT NULL,
                ticker VARCHAR NOT NULL,
                document_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                subject VARCHAR,
                document_type VARCHAR,
                delivery_date TIMESTAMP NOT NULL,
                source_url VARCHAR NOT NULL,
                classification VARCHAR NOT NULL,
                selection_reason VARCHAR NOT NULL,
                document_checksum VARCHAR,
                extraction_status VARCHAR NOT NULL,
                PRIMARY KEY(selection_run_id,ticker,document_id,version)
            )
            """
        )

    @staticmethod
    def _save_selection(
        store: DatabaseStore, run_id: str, ticker: str, document_id: str,
        version: int, subject: str | None, document_type: str | None,
        delivery_date: datetime, source_url: str, classification: str,
        selection_reason: str, checksum: str | None, status: str,
    ) -> None:
        store.connection.execute(
            """
            INSERT OR REPLACE INTO company_exposure_document_selections VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            [
                run_id, ticker, document_id, version, subject, document_type,
                delivery_date, source_url, classification, selection_reason,
                checksum, status,
            ],
        )
