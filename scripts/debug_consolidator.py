import sys, tempfile, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

tmp = tempfile.mkdtemp()
store = DatabaseStore(Path(tmp) / "t.duckdb")
conn = store.connection

# Seed claim
conn.execute("""
    INSERT INTO evidence_claims (
        claim_id, document_id, cvm_code, ticker, claim_type,
        subject, predicate, object_text, numeric_value, unit, currency,
        effective_date, horizon_end, source_page, source_start, source_end,
        source_excerpt, extraction_method, confidence, created_at
    ) VALUES ('C1','D1','001','PETR4','DIVIDEND','s','p','o',1.5,'BRL_PER_SHARE','BRL',
              NULL,NULL,1,0,10,'excerpt','REGEX',0.92,'2026-01-01')
""")
cnt = conn.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
print(f"Claims inserted: {cnt}")

# Try save_event_candidate
try:
    candidate = {
        "event_id": "EVT_TEST001",
        "ticker": "PETR4",
        "cvm_code": "001",
        "event_type": "DIVIDEND_DECLARED",
        "title": "Test event",
        "effective_date": None,
        "claim_ids": ["C1"],
        "evidence_count": 1,
        "novelty_score": 1.0,
        "materiality_score": 0.85,
        "persistence_score": 0.8,
        "quantitative_impact": {"BRL": 1.5},
        "invalidators": [],
        "status": "CANDIDATE",
    }
    store.save_event_candidate(candidate)
    print("save_event_candidate: OK")
except Exception as e:
    print(f"save_event_candidate ERROR: {e}")
    traceback.print_exc()

# Check events
evts = conn.execute("SELECT event_id, event_type FROM event_candidates").fetchall()
print(f"Event candidates: {evts}")

# Now test the full consolidator
from macro_b3_bot.application.consolidate_events import EventConsolidator
settings = Settings(data_dir=Path(tmp))

# Second store for consolidator
store2 = DatabaseStore(Path(tmp) / "t.duckdb")
conn2 = store2.connection
conn2.execute("""
    INSERT INTO evidence_claims (
        claim_id, document_id, cvm_code, ticker, claim_type,
        subject, predicate, object_text, numeric_value, unit, currency,
        effective_date, horizon_end, source_page, source_start, source_end,
        source_excerpt, extraction_method, confidence, created_at
    ) VALUES ('C2','D2','002','VALE3','JCP','s','p','o',0.25,'BRL_PER_SHARE','BRL',
              NULL,NULL,1,0,10,'excerpt','REGEX',0.92,'2026-01-01')
""")

try:
    ec = EventConsolidator(settings)
    res = ec.consolidate()
    print(f"\nConsolidator result: {res}")
except Exception as e:
    print(f"\nConsolidator ERROR: {e}")
    traceback.print_exc()

store2.close()
store.close()
