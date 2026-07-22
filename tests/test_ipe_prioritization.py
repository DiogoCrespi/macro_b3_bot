import sys
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.ipe_models import IpeDocumentIndex
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.application.prioritize_ipe import IpePrioritizer

class TestIpePrioritization(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fato_relevante_receives_high_priority_score(self):
        store = DatabaseStore(self.db_path)
        doc = IpeDocumentIndex(
            document_id="IPE_HIGH_1",
            cvm_code="004170",
            company_name="PETROBRAS",
            category="Fato Relevante",
            subject="Aquisição de novos ativos estratégicos",
            delivery_date=datetime.now(timezone.utc),
            version=1,
            raw_index_checksum="hash_raw",
            record_checksum="hash_rec",
            ingestion_run_id="run_test"
        )
        store.save_ipe_document_index(doc.model_dump(mode="json"))

        # Adiciona vinculo com ticker PETR4
        store.save_ticker_mapping({
            "ticker": "PETR4",
            "cvm_code": "004170",
            "cnpj": "33000167000101",
            "mapping_source": "EXACT_CNPJ",
            "confidence": 1.0,
            "validated": True
        })
        store.close()

        settings = Settings(data_dir=Path(self.temp_dir.name))
        prioritizer = IpePrioritizer(settings)
        res = prioritizer.prioritize_queue(min_score_threshold=0.65)

        self.assertEqual(res["total_processed"], 1)
        self.assertEqual(res["high_priority_queued"], 1)

    def test_low_priority_category(self):
        store = DatabaseStore(self.db_path)
        doc = IpeDocumentIndex(
            document_id="IPE_LOW_1",
            cvm_code="999999",
            company_name="EMPRESA SEM TICKER",
            category="Outros",
            subject="Aviso geral",
            delivery_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            version=1,
            raw_index_checksum="hash_raw2",
            record_checksum="hash_rec2",
            ingestion_run_id="run_test"
        )
        store.save_ipe_document_index(doc.model_dump(mode="json"))
        store.close()

        settings = Settings(data_dir=Path(self.temp_dir.name))
        prioritizer = IpePrioritizer(settings)
        res = prioritizer.prioritize_queue(min_score_threshold=0.65)

        self.assertEqual(res["high_priority_queued"], 0)

    def test_judicial_recovery_term_priority_boost(self):
        store = DatabaseStore(self.db_path)
        doc = IpeDocumentIndex(
            document_id="IPE_RJ_1",
            cvm_code="001122",
            company_name="OI S.A.",
            category="Informações de Companhias em Recuperação Judicial ou Extrajudicial",
            subject="Pedido de Recuperação Judicial e reestruturação de dívida",
            delivery_date=datetime.now(timezone.utc),
            version=1,
            raw_index_checksum="hash_rj",
            record_checksum="hash_rj_rec",
            ingestion_run_id="run_rj"
        )
        store.save_ipe_document_index(doc.model_dump(mode="json"))
        store.close()

        settings = Settings(data_dir=Path(self.temp_dir.name))
        prioritizer = IpePrioritizer(settings)
        res = prioritizer.prioritize_queue(min_score_threshold=0.65)

        self.assertEqual(res["high_priority_queued"], 1)

if __name__ == "__main__":
    unittest.main()
