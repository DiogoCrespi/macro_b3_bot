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

from macro_b3_bot.domain.ipe_models import IpeProcessingState
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestIpePrioritization(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"
        self.store = DatabaseStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_decomposed_score_formula_constraint(self):
        state = IpeProcessingState(
            document_id="DOC_DECOMPOSED_1",
            status="QUEUED",
            priority_score=0.945,
            category_score=1.0,
            recency_score=0.9,
            ticker_mapping_score=1.0,
            liquidity_score=0.8,
            material_terms_score=1.0,
            updated_at=datetime.now(timezone.utc)
        )
        self.store.save_ipe_processing_state(state.model_dump(mode="json"))

        row = self.store.connection.execute(
            "SELECT priority_score, category_score, recency_score, ticker_mapping_score, liquidity_score, material_terms_score FROM ipe_processing_queue WHERE document_id = ?",
            ["DOC_DECOMPOSED_1"]
        ).fetchone()

        p_score, cat_s, rec_s, tick_s, liq_s, mat_s = row
        expected = (0.30 * cat_s) + (0.25 * rec_s) + (0.20 * tick_s) + (0.15 * liq_s) + (0.10 * mat_s)
        self.assertAlmostEqual(p_score, expected, places=4)

if __name__ == "__main__":
    unittest.main()
