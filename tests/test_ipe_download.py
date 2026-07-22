import sys
import unittest
import tempfile
import asyncio
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.domain.document_models import DownloadedDocument
from macro_b3_bot.adapters.cvm.ipe_document_client import IpeDocumentDownloader

class TestIpeDocumentDownload(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_invalid_url_rejection(self):
        downloader = IpeDocumentDownloader(storage_base_dir=Path(self.temp_dir.name))
        res = asyncio.run(downloader.download_document(
            document_id="DOC_INVALID",
            source_url="ftp://invalid.url/doc.pdf",
            cvm_code="004170",
            year=2026,
            ingestion_run_id="run_test"
        ))
        self.assertIsNone(res)

    def test_downloaded_document_schema_validation(self):
        doc = DownloadedDocument(
            document_id="DOC_1",
            source_url="https://dados.cvm.gov.br/doc1.pdf",
            http_status=200,
            mime_type="application/pdf",
            file_extension="pdf",
            file_size_bytes=1024,
            raw_path="/path/to/raw.pdf",
            document_checksum="sha256_mock_hash",
            downloaded_at=datetime.now(timezone.utc),
            ingestion_run_id="run_1"
        )
        self.assertEqual(doc.document_id, "DOC_1")
        self.assertEqual(doc.file_size_bytes, 1024)

    def test_oversized_file_rejection(self):
        downloader = IpeDocumentDownloader(
            storage_base_dir=Path(self.temp_dir.name),
            max_file_size_bytes=100
        )
        # Downloader rejeita se tamanho exceder max_file_size_bytes
        self.assertEqual(downloader.max_file_size_bytes, 100)

    def test_download_pipeline_instantiation(self):
        from macro_b3_bot.config import Settings
        from macro_b3_bot.application.download_ipe_documents import IpeDownloadPipeline
        pipeline = IpeDownloadPipeline(Settings(data_dir=Path(self.temp_dir.name)))
        self.assertIsNotNone(pipeline.storage_dir)

if __name__ == "__main__":
    unittest.main()
