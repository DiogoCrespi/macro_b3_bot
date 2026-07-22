import sys
import duckdb
from pathlib import Path

db_path = Path("data/audit.duckdb")
conn = duckdb.connect(str(db_path))

print("=== TABLES ===")
tables = conn.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1"
).fetchall()
for (t,) in tables:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} rows")

print("\n=== QUEUE STATUS DISTRIBUTION ===")
rows = conn.execute(
    "SELECT status, COUNT(*) FROM ipe_processing_queue GROUP BY status ORDER BY 2 DESC"
).fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

print("\n=== DOWNLOADED DOCS SAMPLE ===")
try:
    rows = conn.execute(
        "SELECT document_id, mime_type, raw_path FROM downloaded_documents LIMIT 5"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]} | {r[1]} | {r[2]}")
except Exception as e:
    print(f"  [ERRO] {e}")

conn.close()
