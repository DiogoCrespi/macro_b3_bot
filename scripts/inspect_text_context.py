import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import duckdb
conn = duckdb.connect("data/audit.duckdb")

# Pega um documento com 'jcp' e mostra o trecho relevante
rows = conn.execute("""
    SELECT document_id, extracted_text
    FROM extracted_documents
    WHERE LOWER(extracted_text) LIKE '%jcp%'
       OR LOWER(extracted_text) LIKE '%dividendo%'
    LIMIT 2
""").fetchall()

for doc_id, text in rows:
    print(f"\n=== {doc_id} ===")
    # Encontra o contexto ao redor de 'jcp' ou 'dividendo'
    lower = text.lower()
    for kw in ["jcp", "dividendo", "r$", "por ação", "por cota"]:
        idx = lower.find(kw)
        if idx >= 0:
            start = max(0, idx - 200)
            end = min(len(text), idx + 400)
            print(f"\n  [keyword: '{kw}' @ {idx}]")
            print(f"  ...{text[start:end]}...")
            break

conn.close()
