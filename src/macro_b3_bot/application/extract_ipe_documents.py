from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Sequence

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.document_models import ExtractedDocument
from macro_b3_bot.adapters.cvm.extractors.html_extractor import HtmlExtractor
from macro_b3_bot.adapters.cvm.extractors.pdf_extractor import PdfExtractor
from macro_b3_bot.adapters.cvm.extractors.text_extractor import TextExtractor
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum
from macro_b3_bot.infrastructure.store import DatabaseStore

# Magic bytes para detecção real de tipo
_PDF_MAGIC  = b"%PDF-"
_HTML_MAGIC = (b"<", b"<!",)

class IpeExtractionPipeline:
    """
    Pipeline de extração textual de documentos baixados (HTML, PDF, TXT).
    Detecta o tipo real pelo magic byte do arquivo, ignorando o MIME type
    reportado pela CVM (que frequentemente serve PDFs como text/html).
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"
        self.html_extractor = HtmlExtractor()
        self.pdf_extractor  = PdfExtractor()
        self.text_extractor = TextExtractor()

    def _detect_extractor(self, content_bytes: bytes, file_path: Path):
        """Retorna (extractor, method_name) baseado no magic byte real do arquivo."""
        header = content_bytes[:8].lstrip()
        if header.startswith(_PDF_MAGIC):
            return self.pdf_extractor, "PDF_DIRECT"
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return self.pdf_extractor, "PDF_DIRECT"
        if suffix in (".html", ".htm"):
            return self.html_extractor, "HTML_PARSER"
        # Heurística: se começa com '<' provavelmente é HTML
        if header.startswith(b"<"):
            return self.html_extractor, "HTML_PARSER"
        return self.text_extractor, "TEXT_PARSER"

    def extract_downloaded_batch(
        self,
        limit: int = 500,
        document_ids: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Seleciona documentos baixados pendentes de extração
        params: list[object] = []
        document_filter = ""
        if document_ids is not None:
            if not document_ids:
                store.close()
                return {"total_processed": 0, "extracted_count": 0}
            placeholders = ",".join("?" for _ in document_ids)
            document_filter = f"AND d.document_id IN ({placeholders})"
            params.extend(document_ids)
        params.append(limit)
        rows = conn.execute(f"""
            SELECT d.document_id, d.document_checksum, d.mime_type, d.raw_path
            FROM downloaded_documents d
            JOIN ipe_processing_queue q USING (document_id)
            WHERE q.status = 'DOWNLOADED'
              {document_filter}
            ORDER BY d.document_id,d.downloaded_at DESC
            LIMIT ?
        """, params).fetchall()

        total_processed = len(rows)
        extracted_count = 0

        for doc_id, doc_checksum, mime_type, raw_path_str in rows:
            file_path = Path(raw_path_str)
            if not file_path.exists():
                conn.execute(
                    "UPDATE ipe_processing_queue SET status = 'EXTRACTION_FAILED', updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                    [doc_id]
                )
                continue

            content_bytes = file_path.read_bytes()
            extractor, method = self._detect_extractor(content_bytes, file_path)

            try:
                text, pages, quality = extractor.extract_text(content_bytes)
            except Exception as e:
                conn.execute(
                    "UPDATE ipe_processing_queue SET status = 'EXTRACTION_FAILED', last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                    [str(e)[:500], doc_id]
                )
                continue

            norm_checksum = compute_raw_checksum(text)

            extracted_doc = ExtractedDocument(
                document_id=doc_id,
                document_checksum=doc_checksum,
                extraction_method=method,
                text=text,
                text_length=len(text),
                page_count=pages,
                language="pt",
                normalized_text_checksum=norm_checksum,
                extraction_quality=quality,
                extracted_at=datetime.now(timezone.utc)
            )

            store.save_extracted_document(extracted_doc.model_dump(mode="json"))

            # Atualiza status na fila
            new_status = "EXTRACTED" if quality >= 0.50 else "OCR_REQUIRED"
            conn.execute(
                "UPDATE ipe_processing_queue SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                [new_status, doc_id]
            )
            extracted_count += 1

        store.close()

        return {
            "total_processed": total_processed,
            "extracted_count": extracted_count
        }
