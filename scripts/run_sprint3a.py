"""
Sprint 3A: Full pipeline execution
1. Extract text from 295 DOWNLOADED documents
2. Deduplicate
3. Build EvidenceClaims
4. Generate EventCandidates
"""
import sys
import traceback
from pathlib import Path
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.adapters.cvm.extractors.html_extractor import HtmlExtractor
from macro_b3_bot.adapters.cvm.extractors.pdf_extractor import PdfExtractor
from macro_b3_bot.adapters.cvm.extractors.text_extractor import TextExtractor
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum

settings = Settings()
db_path = settings.data_dir / "audit.duckdb"
store = DatabaseStore(db_path)
conn = store.connection

print("=" * 70)
print(" SPRINT 3A: EXTRAÇÃO + DEDUP + EVIDÊNCIAS")
print("=" * 70)

# ------------------------------------------------------------------ #
# 1. EXTRAÇÃO
# ------------------------------------------------------------------ #
print("\n[1/3] Extraindo texto de documentos DOWNLOADED...")

rows = conn.execute("""
    SELECT d.document_id, d.document_checksum, d.mime_type, d.raw_path
    FROM downloaded_documents d
    JOIN ipe_processing_queue q USING (document_id)
    WHERE q.status = 'DOWNLOADED'
""").fetchall()

print(f"  Documentos pendentes de extração: {len(rows)}")

html_ext = HtmlExtractor()
pdf_ext  = PdfExtractor()
txt_ext  = TextExtractor()

ext_ok = 0
ext_fail = 0
ext_skip = 0

for doc_id, doc_checksum, mime_type, raw_path_str in rows:
    file_path = Path(raw_path_str)
    if not file_path.exists():
        conn.execute(
            "UPDATE ipe_processing_queue SET status='EXTRACTION_FAILED', updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
            [doc_id]
        )
        ext_fail += 1
        continue

    try:
        content_bytes = file_path.read_bytes()
        suffix = file_path.suffix.lower()
        mime_clean = (mime_type or "").lower()

        if "pdf" in mime_clean or suffix == ".pdf":
            extractor, method = pdf_ext, "PDF_DIRECT"
        elif "html" in mime_clean or suffix in (".html", ".htm"):
            extractor, method = html_ext, "HTML_PARSER"
        else:
            extractor, method = txt_ext, "TEXT_PARSER"

        text, pages, quality = extractor.extract_text(content_bytes)
        norm_checksum = compute_raw_checksum(text)

        saved = store.save_extracted_document({
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

        if saved:
            ext_ok += 1
        else:
            ext_skip += 1  # já existia

    except Exception as e:
        conn.execute(
            "UPDATE ipe_processing_queue SET status='EXTRACTION_FAILED', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
            [str(e)[:500], doc_id]
        )
        ext_fail += 1

print(f"  ✓ Extraídos (novos): {ext_ok} | Já existiam: {ext_skip} | Falhas: {ext_fail}")

# ------------------------------------------------------------------ #
# 2. DEDUPLICAÇÃO
# ------------------------------------------------------------------ #
print("\n[2/3] Deduplicando documentos extraídos...")

from macro_b3_bot.application.deduplicate_documents import IpeDeduplicationPipeline, _compute_jaccard_similarity

ded_rows = conn.execute("""
    SELECT document_id, document_checksum, normalized_text_checksum, extracted_text
    FROM extracted_documents
""").fetchall()

seen_files = {}
seen_texts = {}
text_corpus = []
dup_file = dup_text = dup_near = 0

for doc_id, doc_hash, text_hash, text in ded_rows:
    if doc_hash in seen_files:
        store.save_duplicate_link({
            "canonical_document_id": seen_files[doc_hash],
            "duplicate_document_id": doc_id,
            "duplicate_type": "EXACT_FILE_DUPLICATE",
            "similarity": 1.0
        })
        dup_file += 1
        continue
    else:
        seen_files[doc_hash] = doc_id

    if text_hash in seen_texts:
        store.save_duplicate_link({
            "canonical_document_id": seen_texts[text_hash],
            "duplicate_document_id": doc_id,
            "duplicate_type": "EXACT_TEXT_DUPLICATE",
            "similarity": 1.0
        })
        dup_text += 1
        continue
    else:
        seen_texts[text_hash] = doc_id

    is_near = False
    # Jaccard só para textos menores (<=100k chars) por performance
    if len(text) <= 100_000:
        for prev_id, prev_text in text_corpus:
            sim = _compute_jaccard_similarity(text, prev_text)
            if sim >= 0.95:
                store.save_duplicate_link({
                    "canonical_document_id": prev_id,
                    "duplicate_document_id": doc_id,
                    "duplicate_type": "NEAR_DUPLICATE",
                    "similarity": round(sim, 4)
                })
                dup_near += 1
                is_near = True
                break

    if not is_near:
        text_corpus.append((doc_id, text))
        conn.execute(
            "UPDATE ipe_processing_queue SET status='DEDUPLICATED', updated_at=CURRENT_TIMESTAMP WHERE document_id=?",
            [doc_id]
        )

print(f"  ✓ Canônicos: {len(text_corpus)} | Dup-arquivo: {dup_file} | Dup-texto: {dup_text} | Quase-dup: {dup_near}")

# ------------------------------------------------------------------ #
# 3. EVIDÊNCIAS (EvidenceClaims)
# ------------------------------------------------------------------ #
print("\n[3/3] Construindo EvidenceClaims...")

from macro_b3_bot.application.build_evidence import IpeEvidenceBuilder

ev_builder = IpeEvidenceBuilder(settings)
ev_res = ev_builder.build_evidence_batch(limit=2000)
print(f"  ✓ Documentos avaliados: {ev_res['documents_processed']} | Claims gerados: {ev_res['claims_generated']}")

# ------------------------------------------------------------------ #
# RELATÓRIO FINAL
# ------------------------------------------------------------------ #
total_extracted = conn.execute("SELECT COUNT(*) FROM extracted_documents").fetchone()[0]
total_claims = conn.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
total_events = conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0]

queue_dist = conn.execute(
    "SELECT status, COUNT(*) FROM ipe_processing_queue GROUP BY status ORDER BY 2 DESC"
).fetchall()

claim_types = conn.execute(
    "SELECT claim_type, COUNT(*) FROM evidence_claims GROUP BY claim_type ORDER BY 2 DESC"
).fetchall()

store.close()

print("\n" + "=" * 70)
print(" RELATÓRIO SPRINT 3A")
print("=" * 70)
print(f"Documentos extraídos total:     {total_extracted}")
print(f"EvidenceClaims total:           {total_claims}")
print(f"EventCandidates total:          {total_events}")
print("\nDistribuição da fila:")
for status, cnt in queue_dist:
    print(f"  {status:<25} {cnt:>6}")
print("\nTipos de claim:")
for claim_type, cnt in claim_types:
    print(f"  {claim_type:<25} {cnt:>6}")
print("=" * 70)
