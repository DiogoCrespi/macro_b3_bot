"""
Re-extração completa com detecção por magic bytes.
Limpa extracted_documents e re-processa todos os 295 documentos.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import duckdb
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.adapters.cvm.extractors.html_extractor import HtmlExtractor
from macro_b3_bot.adapters.cvm.extractors.pdf_extractor import PdfExtractor
from macro_b3_bot.adapters.cvm.extractors.text_extractor import TextExtractor
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum

_PDF_MAGIC = b"%PDF-"

settings = Settings()
db_path = settings.data_dir / "audit.duckdb"
store = DatabaseStore(db_path)
conn = store.connection

print("=== RE-EXTRAÇÃO COM MAGIC BYTES ===\n")

# 1. Limpar extrações anteriores e resetar status da fila
conn.execute("DELETE FROM extracted_documents")
conn.execute("DELETE FROM document_duplicate_links")
conn.execute("DELETE FROM evidence_claims")
conn.execute("DELETE FROM event_candidates")
conn.execute("UPDATE ipe_processing_queue SET status = 'DOWNLOADED', updated_at = CURRENT_TIMESTAMP WHERE status != 'DISCOVERED'")
print("✓ Extrações anteriores removidas. Fila resetada para DOWNLOADED.")

html_ext = HtmlExtractor()
pdf_ext  = PdfExtractor()
txt_ext  = TextExtractor()

rows = conn.execute("""
    SELECT d.document_id, d.document_checksum, d.raw_path
    FROM downloaded_documents d
    JOIN ipe_processing_queue q USING (document_id)
    WHERE q.status = 'DOWNLOADED'
""").fetchall()

print(f"Documentos para re-extração: {len(rows)}\n")

pdf_count = html_count = txt_count = ok_count = fail_count = ocr_count = 0

for doc_id, doc_checksum, raw_path_str in rows:
    file_path = Path(raw_path_str)
    if not file_path.exists():
        conn.execute(
            "UPDATE ipe_processing_queue SET status='EXTRACTION_FAILED', updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
            [doc_id]
        )
        fail_count += 1
        continue

    content_bytes = file_path.read_bytes()
    header = content_bytes[:8].lstrip()

    if header.startswith(_PDF_MAGIC):
        extractor, method = pdf_ext, "PDF_DIRECT"
        pdf_count += 1
    elif header.startswith(b"<"):
        extractor, method = html_ext, "HTML_PARSER"
        html_count += 1
    else:
        extractor, method = txt_ext, "TEXT_PARSER"
        txt_count += 1

    try:
        text, pages, quality = extractor.extract_text(content_bytes)
        norm_checksum = compute_raw_checksum(text)

        store.save_extracted_document({
            "document_id": doc_id,
            "document_checksum": doc_checksum,
            "extraction_method": method,
            "text": text,
            "text_length": len(text),
            "page_count": pages,
            "language": "pt",
            "normalized_text_checksum": norm_checksum,
            "extraction_quality": quality,
            "extracted_at": datetime.now(timezone.utc).isoformat()
        })

        new_status = "EXTRACTED" if quality >= 0.50 else "OCR_REQUIRED"
        conn.execute(
            "UPDATE ipe_processing_queue SET status=?, updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
            [new_status, doc_id]
        )
        ok_count += 1
        if new_status == "OCR_REQUIRED":
            ocr_count += 1

    except Exception as e:
        conn.execute(
            "UPDATE ipe_processing_queue SET status='EXTRACTION_FAILED', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
            [str(e)[:400], doc_id]
        )
        fail_count += 1

print("Resultados:")
print(f"  PDF (magic):   {pdf_count}")
print(f"  HTML (magic):  {html_count}")
print(f"  TXT (fallback): {txt_count}")
print(f"  ✓ OK:          {ok_count} (OCR necessário: {ocr_count})")
print(f"  ✗ Falhas:      {fail_count}")

# Amostra de texto extraído de um PDF
print("\n=== AMOSTRA DE TEXTO EXTRAÍDO DE PDF ===")
sample = conn.execute("""
    SELECT document_id, extraction_method, text_length, extraction_quality,
           SUBSTRING(extracted_text, 1, 600)
    FROM extracted_documents
    WHERE extraction_method = 'PDF_DIRECT'
    LIMIT 1
""").fetchone()

if sample:
    doc_id, method, length, quality, snippet = sample
    print(f"  Doc: {doc_id}")
    print(f"  Método: {method} | Length: {length} | Quality: {quality}")
    print(f"  Texto:\n  {snippet}")
else:
    print("  Nenhum PDF extraído ainda")

# Palavras-chave nos textos re-extraídos
print("\n=== PALAVRAS FINANCEIRAS APÓS RE-EXTRAÇÃO ===")
keywords = ["dividendo", "jcp", "juros sobre capital", "recompra", "debênture", "emissão de ações"]
for kw in keywords:
    cnt = conn.execute(
        "SELECT COUNT(*) FROM extracted_documents WHERE LOWER(extracted_text) LIKE ?",
        [f"%{kw}%"]
    ).fetchone()[0]
    print(f"  '{kw}': {cnt} documentos")

store.close()
