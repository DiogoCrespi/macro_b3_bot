import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import duckdb
conn = duckdb.connect("data/audit.duckdb")

# Pega categorias e assuntos dos documentos extraídos
print("=== CATEGORIA / ASSUNTO DOS DOCUMENTOS EXTRAÍDOS ===")
rows = conn.execute("""
    SELECT i.category, i.subject, COUNT(*) as cnt
    FROM extracted_documents e
    JOIN ipe_document_index i USING (document_id)
    GROUP BY i.category, i.subject
    ORDER BY cnt DESC
    LIMIT 30
""").fetchall()
for r in rows:
    print(f"  [{r[0]}] {r[1]}: {r[2]}")

# Pega amostra de texto de um doc de dividendo (se houver)
print("\n=== AMOSTRA DE TEXTO (primeiros 3 docs com 'dividendo' ou 'JCP' no texto) ===")
samples = conn.execute("""
    SELECT e.document_id, i.category, i.subject, 
           SUBSTRING(e.extracted_text, 1, 800) as snippet
    FROM extracted_documents e
    JOIN ipe_document_index i USING (document_id)
    WHERE LOWER(e.extracted_text) LIKE '%dividendo%'
       OR LOWER(e.extracted_text) LIKE '%jcp%'
       OR LOWER(e.extracted_text) LIKE '%juros sobre%'
    LIMIT 3
""").fetchall()

if not samples:
    print("  NENHUM documento com 'dividendo', 'jcp' ou 'juros sobre' no texto!")
    # Mostra amostra de texto puro do primeiro doc
    print("\n=== TEXTO BRUTO DOS PRIMEIROS 2 DOCUMENTOS ===")
    raw = conn.execute("""
        SELECT document_id, SUBSTRING(extracted_text, 1, 1500) FROM extracted_documents LIMIT 2
    """).fetchall()
    for r in raw:
        print(f"\n  --- {r[0]} ---\n  {r[1]}")
else:
    for doc_id, cat, subj, snippet in samples:
        print(f"\n  --- {doc_id} | {cat} | {subj} ---\n  {snippet}")

# Palavras-chave reais nos textos
print("\n=== PALAVRAS FINANCEIRAS ENCONTRADAS ===")
keywords = ["dividendo", "jcp", "juros sobre", "recompra", "buyback", "capital", "debênture", "emissão"]
for kw in keywords:
    cnt = conn.execute(f"""
        SELECT COUNT(*) FROM extracted_documents
        WHERE LOWER(extracted_text) LIKE '%{kw}%'
    """).fetchone()[0]
    print(f"  '{kw}': {cnt} documentos")

conn.close()
