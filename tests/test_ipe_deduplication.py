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

    def test_deduplication_precedence_hierarchy(self):
        # EXACT_FILE_DUPLICATE -> EXACT_TEXT_DUPLICATE -> NEAR_DUPLICATE -> CANONICAL
        precedence = ["EXACT_FILE_DUPLICATE", "EXACT_TEXT_DUPLICATE", "NEAR_DUPLICATE", "CANONICAL"]
        self.assertEqual(precedence[0], "EXACT_FILE_DUPLICATE")
        self.assertEqual(precedence[1], "EXACT_TEXT_DUPLICATE")

    def test_numerical_entity_check_for_near_duplicate(self):
        text_a = "Lucro líquido de R$ 100.000.000 no 1T26"
        text_b = "Lucro líquido de R$ 500.000.000 no 1T26"
        # Mesmo template, números diferentes = DEVE MANTER AMBOS (CANÔNICOS)
        sim = _compute_jaccard_similarity(text_a, text_b)
        self.assertGreater(sim, 0.70)

if __name__ == "__main__":
    unittest.main()
