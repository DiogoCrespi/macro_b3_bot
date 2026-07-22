import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.adapters.cvm.extractors.base import normalize_document_text
from macro_b3_bot.adapters.cvm.extractors.html_extractor import HtmlExtractor
from macro_b3_bot.adapters.cvm.extractors.text_extractor import TextExtractor

class TestIpeTextExtraction(unittest.TestCase):

    def test_unicode_nfkc_normalization(self):
        raw = "PETROBRAS\xa0\xa0S.A.\n\n\n\nAprovação   de   Dividendos  R$ 1.50"
        normalized = normalize_document_text(raw)
        self.assertEqual(normalized, "PETROBRAS S.A.\n\nAprovação de Dividendos R$ 1.50")

    def test_html_extractor_script_and_style_stripping(self):
        html_bytes = b"<html><head><style>body {color: red;}</style></head><body><script>var x = 1;</script><h1>Fato Relevante</h1><p>Lucro de R$ 10.000.000</p></body></html>"
        extractor = HtmlExtractor()
        text, pages, quality = extractor.extract_text(html_bytes)
        self.assertNotIn("color: red", text)
        self.assertNotIn("var x = 1", text)
        self.assertIn("Fato Relevante", text)
        self.assertEqual(pages, 1)

    def test_text_extractor_utf8(self):
        txt_bytes = "Comunicado ao Mercado: Aumento de Capital".encode("utf-8")
        extractor = TextExtractor()
        text, pages, quality = extractor.extract_text(txt_bytes)
        self.assertEqual(text, "Comunicado ao Mercado: Aumento de Capital")
        self.assertEqual(quality, 1.0)

if __name__ == "__main__":
    unittest.main()
