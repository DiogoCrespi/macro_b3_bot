from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_b3_bot.infrastructure.store import DatabaseStore

def populate():
    db_path = Path("data/macro_b3_bot.duckdb")
    store = DatabaseStore(db_path)

    store.connection.execute("""
        INSERT OR REPLACE INTO macro_releases (
            release_id, source, series_code, indicator, geography, frequency, unit,
            reference_date, available_at, actual_value, raw_checksum, record_checksum, ingestion_run_id
        ) VALUES (
            'rel_bcb_ipca_202607', 'BCB', '433', 'IPCA', '["BR"]', 'MONTHLY', '%',
            '2026-07-01', '2026-07-15 10:00:00', 0.45, 'hash1', 'hash2', 'run_001'
        )
    """)

    store.connection.execute("""
        INSERT OR REPLACE INTO evidence_claims (
            claim_id, document_id, cvm_code, ticker, claim_type, subject, predicate,
            object_text, source_excerpt, extraction_method, confidence, created_at
        ) VALUES (
            'claim_ipca_acceleration_001', 'doc_cvm_itr_202607', '012345', 'PETR4', 'MACRO', 'Inflation', 'rose',
            'IPCA accelerated to 0.45%', 'Excerpt text describing inflation', 'LLM_EXTRACTOR', 0.90, '2026-07-15 10:30:00'
        )
    """)

    store.connection.execute("""
        INSERT OR REPLACE INTO sector_state_snapshots (
            snapshot_id, sector, as_of_timestamp, net_impact, bullish_impact, bearish_impact,
            conflict_ratio, supporting_event_ids, opposing_event_ids, confidence, status, run_id, graph_version
        ) VALUES (
            'sec_retail_202607', 'RETAIL', '2026-07-15 11:00:00', 0.25, 0.50, 0.25,
            0.10, '["rel_bcb_ipca_202607"]', '[]', 0.85, 'VALID', 'run_001', '1.0.0'
        )
    """)

    store.connection.execute("""
        INSERT OR REPLACE INTO cvm_documents (
            document_id, document_type, cvm_code, cnpj, reference_date, received_at, filing_available_at,
            version, raw_zip_checksum, ingestion_run_id
        ) VALUES (
            'doc_cvm_itr_202607', 'ITR', '012345', '00000000000000', '2026-06-30', '2026-07-14 18:00:00', '2026-07-14 18:00:00',
            1, 'zip_hash', 'run_001'
        )
    """)

    print("PIT sample data populated in DuckDB.")
    store.close()

if __name__ == "__main__":
    populate()
