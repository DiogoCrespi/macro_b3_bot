import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone
import duckdb

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.application.consolidate_events import EventConsolidator


def _setup_store(db_dir: str) -> DatabaseStore:
    db_path = Path(db_dir) / "audit.duckdb"
    store = DatabaseStore(db_path)
    store._init_tables()
    return store


def _seed_claim(store: DatabaseStore, claim_id: str, cvm_code: str,
                ticker: str, claim_type: str, numeric_value: float, announcement_date: str):
    """Insere um EvidenceClaim de teste com o documento correspondente no ipe_document_index."""
    d_id = f"DOC_{claim_id}"
    store.connection.execute("""
        INSERT OR IGNORE INTO ipe_document_index (
            document_id, cvm_code, company_name, category, document_type,
            delivery_date, version, raw_index_checksum, record_checksum, ingestion_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        d_id, cvm_code, f"COMPANY_{cvm_code}", "Fato Relevante", "IPE",
        f"{announcement_date} 09:30:00", 1, "raw_hash", "rec_hash", "run_id"
    ])

    store.connection.execute("""
        INSERT OR IGNORE INTO evidence_claims (
            claim_id, document_id, cvm_code, ticker, claim_type,
            subject, predicate, object_text, numeric_value, unit, currency,
            effective_date, horizon_end, source_page, source_start, source_end,
            source_excerpt, extraction_method, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        claim_id, d_id, cvm_code, ticker, claim_type,
        "Test Subject", "HAS_PAYMENT", f"{claim_type} R${numeric_value}",
        numeric_value, "BRL_PER_SHARE", "BRL",
        None, None, 1, 0, 100,
        f"Test excerpt for {claim_id}",
        "REGEX_TEST", 0.92, datetime.now(timezone.utc).isoformat()
    ])


class TestPseudoReplication(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.tmp.name))
        self.store = _setup_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _consolidator(self):
        return EventConsolidator(self.settings)

    def test_fiqe3_debt_and_capital_increase_grouped(self):
        """FIQE3: Emissão de dívida e aumento de capital no mesmo dia -> 1 EventCandidate (CAPITAL_INCREASE)."""
        _seed_claim(self.store, "CLM_FIQE_1", "26050", "FIQE3", "DEBT_ISSUANCE", 50000000.0, "2025-12-29")
        _seed_claim(self.store, "CLM_FIQE_2", "26050", "FIQE3", "CAPITAL_INCREASE", 200000000.0, "2025-12-29")

        ec = self._consolidator()
        ec.consolidate()

        candidates = self.store.connection.execute(
            "SELECT event_type, effective_date, evidence_count FROM event_candidates WHERE cvm_code = '26050'"
        ).fetchall()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "CAPITAL_INCREASE")  # CAPITAL_INCREASE tem maior prioridade que DEBT_ISSUANCE
        self.assertEqual(str(candidates[0][1]), "2025-12-29")
        self.assertEqual(candidates[0][2], 2)

    def test_pgmn3_jcp_and_capital_increase_grouped(self):
        """PGMN3: JCP e aumento de capital no mesmo dia -> 1 EventCandidate (JCP_DECLARED)."""
        _seed_claim(self.store, "CLM_PGMN_1", "22608", "PGMN3", "CAPITAL_INCREASE", 5.51, "2025-12-18")
        _seed_claim(self.store, "CLM_PGMN_2", "22608", "PGMN3", "JCP", 0.2585, "2025-12-18")

        ec = self._consolidator()
        ec.consolidate()

        candidates = self.store.connection.execute(
            "SELECT event_type, effective_date, evidence_count FROM event_candidates WHERE cvm_code = '22608'"
        ).fetchall()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "JCP_DECLARED")  # JCP tem maior prioridade que CAPITAL_INCREASE
        self.assertEqual(str(candidates[0][1]), "2025-12-18")
        self.assertEqual(candidates[0][2], 2)

    def test_eqtl3_grouped_on_same_date(self):
        """EQTL3: Múltiplas claims na mesma data -> 1 EventCandidate."""
        _seed_claim(self.store, "CLM_EQTL_1", "024821", "EQTL3", "CAPITAL_INCREASE", 1.0, "2025-12-22")
        _seed_claim(self.store, "CLM_EQTL_2", "024821", "EQTL3", "CAPITAL_INCREASE", 1.5, "2025-12-22")

        ec = self._consolidator()
        ec.consolidate()

        candidates = self.store.connection.execute(
            "SELECT event_type, effective_date FROM event_candidates WHERE cvm_code = '024821'"
        ).fetchall()

        self.assertEqual(len(candidates), 1)

    def test_guar3_separate_dates_create_separate_events(self):
        """GUAR3: Diferentes datas -> Eventos diferentes."""
        _seed_claim(self.store, "CLM_GUAR_1", "4669", "GUAR3", "CAPITAL_INCREASE", 310553.75, "2025-12-22")
        _seed_claim(self.store, "CLM_GUAR_2", "4669", "GUAR3", "CAPITAL_INCREASE", 1000000000.0, "2025-12-23")

        ec = self._consolidator()
        ec.consolidate()

        candidates = self.store.connection.execute(
            "SELECT event_type, effective_date FROM event_candidates WHERE cvm_code = '4669' ORDER BY effective_date"
        ).fetchall()

        self.assertEqual(len(candidates), 2)
        self.assertEqual(str(candidates[0][1]), "2025-12-22")
        self.assertEqual(str(candidates[1][1]), "2025-12-23")

    def test_enbr3_grouped_on_same_date(self):
        """ENBR3: Mesma data -> 1 EventCandidate."""
        _seed_claim(self.store, "CLM_ENBR_1", "19763", "ENBR3", "CAPITAL_INCREASE", 1.0, "2025-12-23")
        _seed_claim(self.store, "CLM_ENBR_2", "19763", "ENBR3", "CAPITAL_INCREASE", 2.0, "2025-12-23")

        ec = self._consolidator()
        ec.consolidate()

        candidates = self.store.connection.execute(
            "SELECT event_type, effective_date FROM event_candidates WHERE cvm_code = '19763'"
        ).fetchall()

        self.assertEqual(len(candidates), 1)
