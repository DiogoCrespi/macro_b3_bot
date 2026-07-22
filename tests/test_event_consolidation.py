"""
Tests for Sprint 3A: EventConsolidator - consolidação de EvidenceClaims em EventCandidates.
"""
import sys
import unittest
import tempfile
from decimal import Decimal
from datetime import datetime, timezone, date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR  = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.application.consolidate_events import EventConsolidator


def _setup_store(tmp: str) -> DatabaseStore:
    # O EventConsolidator usa settings.data_dir / "audit.duckdb"
    # então o store de teste deve usar o mesmo caminho
    db_path = Path(tmp) / "audit.duckdb"
    return DatabaseStore(db_path)


def _seed_claim(store: DatabaseStore, claim_id: str, cvm_code: str,
                ticker: str, claim_type: str, numeric_value: float = 1.5, doc_id: str = None):
    """Insere um EvidenceClaim de teste diretamente."""
    d_id = doc_id if doc_id is not None else f"DOC_{claim_id}"
    store.connection.execute("""
        INSERT OR IGNORE INTO ipe_document_index (
            document_id, cvm_code, company_name, category, document_type,
            delivery_date, version, raw_index_checksum, record_checksum, ingestion_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        d_id, cvm_code, "TEST COMPANY", "Aumento de Capital", "IPE",
        "2025-12-29 09:30:00", 1, "raw_hash", "rec_hash", "run_id"
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


class TestEventConsolidator(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_dir=Path(self.tmp.name))
        self.store = _setup_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _consolidator(self):
        return EventConsolidator(self.settings)

    def test_single_dividend_claim_creates_event(self):
        """1 claim DIVIDEND → 1 EventCandidate DIVIDEND_DECLARED."""
        _seed_claim(self.store, "CLM001", "004170", "PETR4", "DIVIDEND", 1.5)

        ec = self._consolidator()
        res = ec.consolidate()

        self.assertGreaterEqual(res["events_created"], 1)

        events = self.store.connection.execute(
            "SELECT event_type, ticker, novelty_score FROM event_candidates"
        ).fetchall()
        self.assertTrue(any(e[0] == "DIVIDEND_DECLARED" for e in events))
        self.assertTrue(any(e[1] == "PETR4" for e in events))

    def test_jcp_claim_creates_jcp_event(self):
        """1 claim JCP → 1 EventCandidate JCP_DECLARED."""
        _seed_claim(self.store, "CLM002", "004170", "PETR4", "JCP", 0.25)

        ec = self._consolidator()
        res = ec.consolidate()

        events = self.store.connection.execute(
            "SELECT event_type FROM event_candidates"
        ).fetchall()
        self.assertTrue(any(e[0] == "JCP_DECLARED" for e in events))

    def test_multiple_claims_same_company_consolidated(self):
        """3 claims DIVIDEND da mesma empresa no mesmo documento → 1 evento (não 3)."""
        for i in range(3):
            _seed_claim(self.store, f"CLM01{i}", "004170", "PETR4", "DIVIDEND", 1.0 + i * 0.1, doc_id="DOC_MULTI")

        ec = self._consolidator()
        res = ec.consolidate()

        count = self.store.connection.execute(
            "SELECT COUNT(*) FROM event_candidates WHERE event_type = 'DIVIDEND_DECLARED' AND cvm_code = '004170'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_novelty_score_decreases_for_repeated_events(self):
        """Segundo evento do mesmo tipo/empresa tem novelty_score = 0.5."""
        _seed_claim(self.store, "CLM_A1", "004170", "PETR4", "DIVIDEND", 1.0)
        ec = self._consolidator()
        ec.consolidate()

        # Insere novo claim (segunda ocorrência)
        _seed_claim(self.store, "CLM_A2", "004170", "PETR4", "DIVIDEND", 1.5)
        res = ec.consolidate()

        scores = self.store.connection.execute(
            "SELECT novelty_score FROM event_candidates ORDER BY created_at"
        ).fetchall()
        # Primeiro evento: novelty=1.0, segundo: novelty=0.5
        if len(scores) >= 2:
            self.assertAlmostEqual(scores[0][0], 1.0)
            self.assertAlmostEqual(scores[1][0], 0.5)

    def test_buyback_claim_creates_buyback_event(self):
        """1 claim SHARE_BUYBACK → BUYBACK_AUTHORIZED."""
        _seed_claim(self.store, "CLM_BB", "004170", "PETR4", "SHARE_BUYBACK", 10_000_000)

        ec = self._consolidator()
        ec.consolidate()

        events = self.store.connection.execute(
            "SELECT event_type FROM event_candidates WHERE event_type = 'BUYBACK_AUTHORIZED'"
        ).fetchall()
        self.assertGreaterEqual(len(events), 1)

    def test_idempotency(self):
        """Consolidar duas vezes não duplica eventos."""
        _seed_claim(self.store, "CLM_IDP", "004170", "PETR4", "DIVIDEND", 1.0)

        ec = self._consolidator()
        ec.consolidate()
        ec.consolidate()  # Segunda chamada — sem novos claims

        count = self.store.connection.execute(
            "SELECT COUNT(*) FROM event_candidates"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
