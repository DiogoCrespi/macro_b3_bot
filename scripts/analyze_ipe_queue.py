import sys
import numpy as np
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

def analyze_queue() -> dict:
    print("\n--------------------------------------------------------------------------")
    print(" 📊 ANALISE E RECALIBRAGEM DA FILA DE DOCUMENTOS IPE CVM (analyze-ipe-queue)")
    print("--------------------------------------------------------------------------\n")
    
    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"

    if not db_path.exists():
        print(f"❌ Banco {db_path} não encontrado!")
        return {}

    store = DatabaseStore(db_path)
    conn = store.connection

    # Carrega todos os scores da fila
    scores = [row[0] for row in conn.execute("SELECT priority_score FROM ipe_processing_queue").fetchall()]

    if not scores:
        print("⚠️ Fila de processamento IPE está vazia!")
        store.close()
        return {}

    scores_arr = np.array(scores)
    p50 = float(np.percentile(scores_arr, 50))
    p75 = float(np.percentile(scores_arr, 75))
    p90 = float(np.percentile(scores_arr, 90))
    p95 = float(np.percentile(scores_arr, 95))
    p99 = float(np.percentile(scores_arr, 99))

    # Filtra piloto 2026 com portões rígidos
    pilot_count = conn.execute("""
        SELECT COUNT(*)
        FROM ipe_processing_queue q
        JOIN ipe_document_index i USING (document_id)
        WHERE q.status IN ('DISCOVERED', 'QUEUED')
          AND i.source_url IS NOT NULL
          AND i.source_url != ''
    """).fetchone()[0]

    print("==========================================================================")
    print("   DISTRIBUICAO DE SCORES E SELECAO PILOTO IPE (analyze-ipe-queue)")
    print("==========================================================================")
    print(f"Documentos totais na fila:      {len(scores):,}")
    print("--------------------------------------------------------------------------")
    print(f"Score P50 (Mediana):            {p50:.4f}")
    print(f"Score P75:                      {p75:.4f}")
    print(f"Score P90:                      {p90:.4f}")
    print(f"Score P95:                      {p95:.4f}")
    print(f"Score P99:                      {p99:.4f}")
    print("--------------------------------------------------------------------------")
    print(f"Elegíveis para Piloto 2026:      {pilot_count:,}")
    print(f"Limite do Lote Piloto:          500 documentos")
    print("==========================================================================\n")

    store.close()
    return {
        "total": len(scores),
        "p50": p50,
        "p75": p75,
        "p90": p90,
        "p95": p95,
        "p99": p99,
        "pilot_count": pilot_count
    }

if __name__ == "__main__":
    analyze_queue()
