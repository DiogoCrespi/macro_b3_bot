"""
Sprint 3A: Build EvidenceClaims + Consolidar EventCandidates nos dados reais.
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

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.application.build_evidence import IpeEvidenceBuilder
from macro_b3_bot.application.consolidate_events import EventConsolidator
from macro_b3_bot.application.deduplicate_documents import IpeDeduplicationPipeline, _compute_jaccard_similarity

settings = Settings()
db_path = settings.data_dir / "audit.duckdb"

print("=" * 70)
print(" SPRINT 3A: EVIDÊNCIAS + EVENTOS (dados reais)")
print("=" * 70)

store = DatabaseStore(db_path)
conn = store.connection

# ------------------------------------------------------------------ #
# 1. Deduplicação (re-rodar após re-extração)
# ------------------------------------------------------------------ #
print("\n[1/3] Deduplicando documentos re-extraídos...")

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
        try:
            store.save_duplicate_link({
                "canonical_document_id": seen_files[doc_hash],
                "duplicate_document_id": doc_id,
                "duplicate_type": "EXACT_FILE_DUPLICATE",
                "similarity": 1.0
            })
        except Exception:
            pass
        dup_file += 1
        continue
    seen_files[doc_hash] = doc_id

    if text_hash in seen_texts:
        try:
            store.save_duplicate_link({
                "canonical_document_id": seen_texts[text_hash],
                "duplicate_document_id": doc_id,
                "duplicate_type": "EXACT_TEXT_DUPLICATE",
                "similarity": 1.0
            })
        except Exception:
            pass
        dup_text += 1
        continue
    seen_texts[text_hash] = doc_id

    is_near = False
    if len(text) <= 100_000:
        for prev_id, prev_text in text_corpus:
            sim = _compute_jaccard_similarity(text, prev_text)
            if sim >= 0.95:
                try:
                    store.save_duplicate_link({
                        "canonical_document_id": prev_id,
                        "duplicate_document_id": doc_id,
                        "duplicate_type": "NEAR_DUPLICATE",
                        "similarity": round(sim, 4)
                    })
                except Exception:
                    pass
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

store.close()

# ------------------------------------------------------------------ #
# 2. Construção de EvidenceClaims
# ------------------------------------------------------------------ #
print("\n[2/3] Construindo EvidenceClaims...")

ev_builder = IpeEvidenceBuilder(settings)
ev_res = ev_builder.build_evidence_batch(limit=2000)
print(f"  ✓ Documentos avaliados: {ev_res['documents_processed']} | Claims gerados: {ev_res['claims_generated']}")

# ------------------------------------------------------------------ #
# 3. Consolidação em EventCandidates
# ------------------------------------------------------------------ #
print("\n[3/3] Consolidando EventCandidates...")

consolidator = EventConsolidator(settings)
cons_res = consolidator.consolidate(limit=5000)
print(f"  ✓ Claims avaliados: {cons_res['claims_evaluated']} | Grupos: {cons_res['groups_formed']} | Eventos criados: {cons_res['events_created']}")

# ------------------------------------------------------------------ #
# RELATÓRIO FINAL
# ------------------------------------------------------------------ #
store2 = DatabaseStore(db_path)
conn2 = store2.connection

total_extracted = conn2.execute("SELECT COUNT(*) FROM extracted_documents").fetchone()[0]
total_claims    = conn2.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
total_events    = conn2.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0]

claim_dist = conn2.execute(
    "SELECT claim_type, COUNT(*) FROM evidence_claims GROUP BY claim_type ORDER BY 2 DESC"
).fetchall()

event_dist = conn2.execute(
    "SELECT event_type, COUNT(*) FROM event_candidates GROUP BY event_type ORDER BY 2 DESC"
).fetchall()

top_events = conn2.execute("""
    SELECT ticker, event_type, materiality_score, novelty_score
    FROM event_candidates
    WHERE ticker IS NOT NULL
    ORDER BY materiality_score DESC
    LIMIT 10
""").fetchall()

queue_dist = conn2.execute(
    "SELECT status, COUNT(*) FROM ipe_processing_queue GROUP BY status ORDER BY 2 DESC"
).fetchall()

store2.close()

print("\n" + "=" * 70)
print(" RELATÓRIO SPRINT 3A — DADOS REAIS")
print("=" * 70)
print(f"Documentos extraídos:          {total_extracted}")
print(f"EvidenceClaims gerados:        {total_claims}")
print(f"EventCandidates consolidados:  {total_events}")

print("\nTipos de Claim:")
for ct, cnt in claim_dist:
    print(f"  {ct:<25} {cnt:>5}")

print("\nTipos de Evento:")
for et, cnt in event_dist:
    print(f"  {et:<30} {cnt:>4}")

print("\nTop 10 Eventos por Materiality Score:")
for ticker, ev_type, mat, nov in top_events:
    print(f"  {ticker:<10} {ev_type:<25} mat={mat:.3f} nov={nov:.1f}")

print("\nFila de Processamento:")
for status, cnt in queue_dist:
    print(f"  {status:<25} {cnt:>6}")

print("=" * 70)
print("  BUY HABILITADO: NÃO (Modo Pesquisa — Sprint 3A Completo)")
print("=" * 70)
