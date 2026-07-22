import sys
import unittest
import tempfile
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.domain.macro_models import MacroObservation, MarketExpectation

class TestBcbExpectationsAndStore(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_duckdb_macro_observations_persistence(self):
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run("RUN_TEST_1", "BCB_SGS")

        obs = MacroObservation(
            source="BCB_SGS",
            series_code="11",
            indicator="selic_daily",
            reference_date=date(2026, 7, 21),
            observed_at=datetime.now(timezone.utc),
            value=Decimal("0.0512"),
            unit="percent",
            frequency="daily",
            revision=0,
            raw_checksum="sha256_mock_checksum",
            ingestion_run_id="RUN_TEST_1"
        )
        
        store.save_macro_observation(obs.model_dump(mode="json"))
        self.assertEqual(store.count_macro_observations(), 1)
        store.close()

    def test_duckdb_market_expectations_persistence(self):
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run("RUN_TEST_2", "BCB_FOCUS")

        exp = MarketExpectation(
            source="BCB_FOCUS",
            indicator="IPCA",
            reference_date=date(2026, 7, 21),
            target_period="2026",
            statistic="Mediana",
            value=Decimal("3.85"),
            base_calculation=0,
            observed_at=datetime.now(timezone.utc),
            raw_checksum="sha256_focus_mock",
            ingestion_run_id="RUN_TEST_2"
        )

        store.save_market_expectation(exp.model_dump(mode="json"))
        self.assertEqual(store.count_market_expectations(), 1)
        store.close()

    def test_idempotency_and_revision_coexistence(self):
        store = DatabaseStore(self.db_path)
        
        # Salva primeira versao
        obs1 = {
            "source": "BCB_SGS", "series_code": "24363", "indicator": "ibc_br",
            "reference_date": date(2026, 1, 1), "observed_at": datetime.now(timezone.utc),
            "value": Decimal("145.2"), "unit": "index", "frequency": "monthly",
            "revision": 0, "raw_checksum": "checksum_v1", "ingestion_run_id": "run_v1"
        }
        store.save_macro_observation(obs1)
        self.assertEqual(store.count_macro_observations(), 1)

        # Salva versao revisada (checksum diferente -> coexistem para auditoria sem look-ahead bias)
        obs2 = {
            "source": "BCB_SGS", "series_code": "24363", "indicator": "ibc_br",
            "reference_date": date(2026, 1, 1), "observed_at": datetime.now(timezone.utc),
            "value": Decimal("145.8"), "unit": "index", "frequency": "monthly",
            "revision": 1, "raw_checksum": "checksum_v2", "ingestion_run_id": "run_v2"
        }
        store.save_macro_observation(obs2)
        self.assertEqual(store.count_macro_observations(), 2)
        store.close()

if __name__ == "__main__":
    unittest.main()
