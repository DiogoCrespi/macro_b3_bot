import sys
import duckdb
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

conn = duckdb.connect("data/audit.duckdb")

print("=== MIME TYPE DISTRIBUTION ===")
rows = conn.execute(
    "SELECT mime_type, COUNT(*) FROM downloaded_documents GROUP BY mime_type ORDER BY 2 DESC"
).fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# Reset PDFs para re-extração
print("\n=== RESETANDO PDFs PARA RE-EXTRAÇÃO ===")
pdf_ids = conn.execute("""
    SELECT d.document_id
    FROM downloaded_documents d
    WHERE d.mime_type LIKE '%pdf%' OR d.file_extension = '.pdf'
""").fetchall()
print(f"  PDFs encontrados: {len(pdf_ids)}")

if pdf_ids:
    # Remove extrações incorretas de PDFs
    for (doc_id,) in pdf_ids:
        conn.execute(
            "DELETE FROM extracted_documents WHERE document_id = ?",
            [doc_id]
        )
        conn.execute(
            "UPDATE ipe_processing_queue SET status = 'DOWNLOADED', updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
            [doc_id]
        )
    print(f"  ✓ {len(pdf_ids)} PDFs resetados para re-extração")
else:
    print("  Nenhum PDF encontrado")

conn.close()
