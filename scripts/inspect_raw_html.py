import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import duckdb
conn = duckdb.connect("data/audit.duckdb")

# Busca documentos por categoria específica e inspeciona o HTML bruto
rows = conn.execute("""
    SELECT d.document_id, d.raw_path, i.category, i.subject
    FROM downloaded_documents d
    JOIN ipe_document_index i USING (document_id)
    WHERE i.category LIKE '%Aviso%' AND i.subject LIKE '%Dividendo%'
    LIMIT 3
""").fetchall()

if not rows:
    print("Sem documentos de Aviso+Dividendo")
    rows = conn.execute("""
        SELECT d.document_id, d.raw_path, i.category, i.subject
        FROM downloaded_documents d
        JOIN ipe_document_index i USING (document_id)
        LIMIT 3
    """).fetchall()

for doc_id, raw_path, cat, subj in rows:
    print(f"\n=== {doc_id} | {cat} | {subj} ===")
    path = Path(raw_path)
    if path.exists():
        content = path.read_bytes()
        print(f"  Tamanho: {len(content)} bytes")
        # Tenta decodificar como HTML
        try:
            text = content[:3000].decode("utf-8", errors="replace")
            print(f"  Primeiros 3000 chars:\n{text}")
        except Exception as e:
            print(f"  Erro: {e}")
    else:
        print(f"  ARQUIVO NÃO EXISTE: {path}")

conn.close()
