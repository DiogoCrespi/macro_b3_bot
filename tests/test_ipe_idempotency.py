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

from macro_b3_bot.domain.ipe_models import IpeDocumentIndex
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestIpeIdempotency(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_duplicate_ipe_document_rejected(self):
        store = DatabaseStore(self.db_path)
        doc = IpeDocumentIndex(
            document_id="IPE_DUP_CHECK",
            cvm_code="004170",
            company_name="PETROBRAS",
            category="Comunicado ao Mercado",
            subject="Esclarecimento sobre notícias",
            delivery_date=datetime.now(timezone.utc),
            version=1,
            raw_index_checksum="raw_checksum_1",
            record_checksum="rec_checksum_1",
            ingestion_run_id="run_1"
        )
        
        # Inserção 1 -> Sucesso
        inserted_1 = store.save_ipe_document_index(doc.model_dump(mode="json"))
        self.assertTrue(inserted_1)
        self.assertEqual(store.count_ipe_documents(), 1)

        # Inserção 2 -> Duplicado (Retorna False)
        inserted_2 = store.save_ipe_document_index(doc.model_dump(mode="json"))
        self.assertFalse(inserted_2)
        self.assertEqual(store.count_ipe_documents(), 1)

        store.close()

if __name__ == "__main__":
    unittest.main()
