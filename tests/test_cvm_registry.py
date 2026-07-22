import sys
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, date, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.domain.cvm_models import CvmCompany, CvmDocument
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestCvmRegistryAndStore(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cvm_company_persistence(self):
        store = DatabaseStore(self.db_path)
        comp = CvmCompany(
            cvm_code="004170",
            cnpj="33000167000101",
            legal_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
            trading_name="PETROBRAS",
            registration_status="ATIVO",
            registration_date=date(1977, 10, 20),
            category="Bolsa",
            collected_at=datetime.now(timezone.utc),
            record_checksum="checksum_petr4",
            ingestion_run_id="run_cad_1"
        )
        was_inserted = store.save_cvm_company(comp.model_dump(mode="json"))
        self.assertTrue(was_inserted)
        self.assertEqual(store.count_cvm_companies(), 1)
        store.close()

    def test_ticker_mapping_confidence(self):
        store = DatabaseStore(self.db_path)
        mapping = {
            "ticker": "PETR4",
            "cvm_code": "004170",
            "cnpj": "33000167000101",
            "mapping_source": "EXACT_CNPJ",
            "confidence": 1.0,
            "validated": True
        }
        store.save_ticker_mapping(mapping)
        self.assertEqual(store.count_ticker_mappings(), 1)
        store.close()

    def test_cvm_document_versioning(self):
        store = DatabaseStore(self.db_path)
        doc1 = CvmDocument(
            document_id="ITR_004170_2026-03-31_v1",
            document_type="ITR",
            cvm_code="004170",
            cnpj="33000167000101",
            reference_date=date(2026, 3, 31),
            received_at=datetime.now(timezone.utc),
            version=1,
            raw_zip_checksum="zip_v1",
            ingestion_run_id="run_itr_1"
        )
        store.save_cvm_document(doc1.model_dump(mode="json"))
        self.assertEqual(store.count_cvm_documents(), 1)
        store.close()

if __name__ == "__main__":
    unittest.main()
