import sys
import unittest
from pathlib import Path
from macro_b3_bot.application.deduplicate_documents import _compute_jaccard_similarity

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

class TestIpeDeduplication(unittest.TestCase):

    def test_jaccard_similarity_exact_and_near(self):
        text1 = "Petrobras aprova pagamento de dividendos de R$ 1.50 por ação ordinária"
        text2 = "Petrobras aprova pagamento de dividendos de R$ 1.50 por ação ordinária"
        text3 = "Petrobras aprova pagamento de dividendos no valor de R$ 1.50 por ação ordinária aos acionistas"

        self.assertEqual(_compute_jaccard_similarity(text1, text2), 1.0)
        self.assertGreater(_compute_jaccard_similarity(text1, text3), 0.70)

if __name__ == "__main__":
    unittest.main()
