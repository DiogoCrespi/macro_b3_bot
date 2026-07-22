import sys
import unittest
import tempfile
from pathlib import Path
from datetime import date, datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.domain.cvm_models import CvmDocument
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestCvmRestatements(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_restatement_version_ordering_and_latest_view(self):
        store = DatabaseStore(self.db_path)
        
        # Versao 1 do ITR (Primeira entrega)
        v1 = CvmDocument(
            document_id="ITR_004170_2026-03-31_v1",
            document_type="ITR",
            cvm_code="004170",
            cnpj="33000167000101",
            reference_date=date(2026, 3, 31),
            received_at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
            version=1,
            raw_zip_checksum="checksum_zip_v1",
            ingestion_run_id="run_1"
        )
        store.save_cvm_document(v1.model_dump(mode="json"))

        # Versao 2 (Reapresentacao posterior da CVM)
        v2 = CvmDocument(
            document_id="ITR_004170_2026-03-31_v2",
            document_type="ITR",
            cvm_code="004170",
            cnpj="33000167000101",
            reference_date=date(2026, 3, 31),
            received_at=datetime(2026, 5, 25, 14, 30, tzinfo=timezone.utc),
            version=2,
            raw_zip_checksum="checksum_zip_v2",
            ingestion_run_id="run_2"
        )
        store.save_cvm_document(v2.model_dump(mode="json"))

        self.assertEqual(store.count_cvm_documents(), 2)

        # Consulta a VIEW latest_cvm_documents (Deve retornar v2 como a versão mais recente)
        latest_res = store.connection.execute(
            "SELECT version, document_id FROM latest_cvm_documents WHERE cvm_code = '004170' AND reference_date = '2026-03-31'"
        ).fetchone()

        self.assertIsNotNone(latest_res)
        self.assertEqual(latest_res[0], 2)
        self.assertEqual(latest_res[1], "ITR_004170_2026-03-31_v2")

        store.close()

if __name__ == "__main__":
    unittest.main()
