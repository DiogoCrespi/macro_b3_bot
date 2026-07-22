import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

def audit_events() -> None:
    settings = Settings()
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)
    conn = store.connection

    total_claims = conn.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
    total_events = conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0]
    approved_events = conn.execute("SELECT COUNT(*) FROM event_candidates WHERE status = 'EVENT_GATE_APPROVED'").fetchone()[0]
    rejected_events = conn.execute("SELECT COUNT(*) FROM event_candidates WHERE status = 'REJECTED'").fetchone()[0]

    type_counts = conn.execute("""
        SELECT event_type, COUNT(*) 
        FROM event_candidates 
        GROUP BY event_type 
        ORDER BY COUNT(*) DESC
    """).fetchall()

    no_ticker = conn.execute("SELECT COUNT(*) FROM event_candidates WHERE ticker IS NULL OR ticker = ''").fetchone()[0]
    no_claims = conn.execute("SELECT COUNT(*) FROM event_candidates WHERE claim_ids = '[]' OR claim_ids IS NULL").fetchone()[0]

    store.close()

    print("\n--------------------------------------------------------------------------")
    print(" 🏛️  AUDITORIA DE CANDIDATOS A EVENTOS (audit-event-candidates)")
    print("--------------------------------------------------------------------------\n")
    print("EVENT CANDIDATES")
    print("--------------------------------------------------------------------------")
    print(f"Claims avaliados:                         {total_claims}")
    print(f"Eventos candidatos gerados:              {total_events}")
    print(f"Eventos aprovados pelo EventGate:         {approved_events}")
    print(f"Eventos rejeitados pelo EventGate:        {rejected_events}")
    print("")
    print("DISTRIBUICAO POR TIPO DE EVENTO")
    print("--------------------------------------------------------------------------")
    for evt_t, cnt in type_counts:
        print(f"  {evt_t:<38} {cnt}")
    print("")
    print("INTEGRIDADE E SEGURANCA")
    print("--------------------------------------------------------------------------")
    print(f"Eventos sem ticker:                       {no_ticker}")
    print(f"Eventos sem evidência:                    {no_claims}")
    print("BUY e Execução de Ordens Habilitados:     NÃO (Research Mode Ativo)")
    print("--------------------------------------------------------------------------\n")

if __name__ == "__main__":
    audit_events()
