import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.adapters.cvm.extractors.html_extractor import HtmlExtractor
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum

settings = Settings()
db_path = settings.data_dir / "audit.duckdb"
store = DatabaseStore(db_path)
conn = store.connection

rows = conn.execute("""
    SELECT d.document_id, d.document_checksum, d.mime_type, d.raw_path
    FROM downloaded_documents d
    JOIN ipe_processing_queue q USING (document_id)
    WHERE q.status = 'DOWNLOADED'
    LIMIT 3
""").fetchall()

print(f"Rows to process: {len(rows)}")

extractor = HtmlExtractor()

for doc_id, doc_checksum, mime_type, raw_path_str in rows:
    print(f"\n--- {doc_id} ---")
    file_path = Path(raw_path_str)
    print(f"  Path: {file_path}")
    print(f"  Exists: {file_path.exists()}")
    
    if not file_path.exists():
        print("  SKIPPED: file not found")
        continue
    
    try:
        content_bytes = file_path.read_bytes()
        print(f"  Size: {len(content_bytes)} bytes")
        text, pages, quality = extractor.extract_text(content_bytes)
        print(f"  Text length: {len(text)}, Pages: {pages}, Quality: {quality}")
        norm_checksum = compute_raw_checksum(text)
        
        doc = {
            "document_id": doc_id,
            "document_checksum": doc_checksum,
            "extraction_method": "HTML_PARSER",
            "text": text,
            "text_length": len(text),
            "page_count": pages,
            "language": "pt",
            "normalized_text_checksum": norm_checksum,
            "extraction_quality": quality,
            "extracted_at": "2026-07-22T00:00:00+00:00"
        }
        result = store.save_extracted_document(doc)
        print(f"  Saved: {result}")
    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()

store.close()
