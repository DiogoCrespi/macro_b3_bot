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

from macro_b3_bot.domain.ipe_models import IpeDocumentIndex, IpeProcessingState
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestIpeIndexAndModels(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ipe_document_index_persistence(self):
        store = DatabaseStore(self.db_path)
        doc = IpeDocumentIndex(
            document_id="IPE_004170_123456_v1",
            cvm_code="004170",
            company_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
            category="Fato Relevante",
            subject="Aprovação de Pagamento de Proventos",
            delivery_date=datetime.now(timezone.utc),
            version=1,
            raw_index_checksum="raw_hash_ipe",
            record_checksum="rec_hash_ipe",
            ingestion_run_id="run_ipe_1"
        )
        was_inserted = store.save_ipe_document_index(doc.model_dump(mode="json"))
        self.assertTrue(was_inserted)
        self.assertEqual(store.count_ipe_documents(), 1)
        store.close()

    def test_ipe_processing_state_queue(self):
        store = DatabaseStore(self.db_path)
        state = IpeProcessingState(
            document_id="IPE_004170_123456_v1",
            status="QUEUED",
            priority_score=0.88,
            attempts=0,
            updated_at=datetime.now(timezone.utc)
        )
        store.save_ipe_processing_state(state.model_dump(mode="json"))
        self.assertEqual(store.count_ipe_queue(), 1)
        store.close()

    def test_ipe_corrupted_data_and_schema_validation(self):
        with self.assertRaises(ValueError):
            IpeProcessingState(
                document_id="INVALID_ID",
                status="INVALID_STATUS",
                priority_score=1.5,
                updated_at=datetime.now(timezone.utc)
            )

    def test_ipe_record_checksum_consistency(self):
        doc1 = IpeDocumentIndex(
            document_id="IPE_1", cvm_code="004170", company_name="PETR4",
            category="Fato Relevante", delivery_date=datetime.now(timezone.utc),
            raw_index_checksum="raw", record_checksum="rec1", ingestion_run_id="run"
        )
        doc2 = IpeDocumentIndex(
            document_id="IPE_1", cvm_code="004170", company_name="PETR4",
            category="Fato Relevante", delivery_date=datetime.now(timezone.utc),
            raw_index_checksum="raw", record_checksum="rec1", ingestion_run_id="run"
        )
        self.assertEqual(doc1.document_id, doc2.document_id)
        self.assertEqual(doc1.record_checksum, doc2.record_checksum)

if __name__ == "__main__":
    unittest.main()
