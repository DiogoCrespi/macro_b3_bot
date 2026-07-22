import sys
import unittest
import tempfile
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.application.build_evidence import IpeEvidenceBuilder

class TestIpeEvidenceBuilder(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_deterministic_dividend_claim_extraction(self):
        builder = IpeEvidenceBuilder(Settings(data_dir=Path(self.temp_dir.name)))
        sample_text = "Fato Relevante: O Conselho de Administração da Petrobras aprovou o pagamento de dividendos de R$ 1,54321 por ação ordinária."

        claims = builder._extract_claims_from_text(
            doc_id="DOC_DIV_1",
            cvm_code="004170",
            ticker="PETR4",
            subject="Aprovação de Proventos",
            text=sample_text
        )

        self.assertGreaterEqual(len(claims), 1)
        dividend_claim = claims[0]
        self.assertEqual(dividend_claim.claim_type, "DIVIDEND")
        self.assertEqual(dividend_claim.numeric_value, Decimal("1.54321"))
        self.assertEqual(dividend_claim.ticker, "PETR4")
        self.assertIn("dividendos de R$ 1,54321 por ação", dividend_claim.source_excerpt)
        self.assertGreaterEqual(dividend_claim.confidence, 0.90)

    def test_deterministic_buyback_claim_extraction(self):
        builder = IpeEvidenceBuilder(Settings(data_dir=Path(self.temp_dir.name)))
        sample_text = "Comunicado ao Mercado: Aprovado o programa de recompra de até 10.000.000 de ações ordinárias."

        claims = builder._extract_claims_from_text(
            doc_id="DOC_BUYBACK_1",
            cvm_code="004170",
            ticker="PETR4",
            subject="Programa de Recompra",
            text=sample_text
        )

        self.assertGreaterEqual(len(claims), 1)
        buyback_claim = claims[0]
        self.assertEqual(buyback_claim.claim_type, "SHARE_BUYBACK")
        self.assertEqual(buyback_claim.numeric_value, Decimal("10000000"))

    def test_jcp_claim_extraction(self):
        builder = IpeEvidenceBuilder(Settings(data_dir=Path(self.temp_dir.name)))
        sample_text = "Comunicado: Aprovado o pagamento de JCP no valor de R$ 0,25 por ação."

        claims = builder._extract_claims_from_text(
            doc_id="DOC_JCP_1", cvm_code="004170", ticker="PETR4",
            subject="JCP", text=sample_text
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_type, "JCP")
        self.assertEqual(claims[0].numeric_value, Decimal("0.25"))

    def test_no_false_positive_claims(self):
        builder = IpeEvidenceBuilder(Settings(data_dir=Path(self.temp_dir.name)))
        sample_text = "Relatório de sustentabilidade e governança corporativa sem valores financeiros."

        claims = builder._extract_claims_from_text(
            doc_id="DOC_CLEAN_1", cvm_code="004170", ticker="PETR4",
            subject="Relatório", text=sample_text
        )
        self.assertEqual(len(claims), 0)

if __name__ == "__main__":
    unittest.main()
