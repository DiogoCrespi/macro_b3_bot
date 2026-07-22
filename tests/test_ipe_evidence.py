"""
Tests for Sprint 3A: EvidenceClaim extraction with calibrated regex.
"""
import sys
import unittest
import tempfile
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR  = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.application.build_evidence import IpeEvidenceBuilder


def _builder(tmp: str) -> IpeEvidenceBuilder:
    return IpeEvidenceBuilder(Settings(data_dir=Path(tmp)))


class TestDividendExtraction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = _builder(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _claims(self, text: str):
        return self.b._extract_claims("DOC1", "004170", "PETR4", "Proventos", text)

    def test_valor_por_acao_ordinaria(self):
        text = "O Conselho aprovou dividendos de R$ 1,54321 por ação ordinária."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(c.claim_type, "DIVIDEND")
        self.assertEqual(c.numeric_value, Decimal("1.54321"))
        self.assertEqual(c.ticker, "PETR4")
        self.assertGreaterEqual(c.confidence, 0.90)

    def test_valor_por_cota_fii(self):
        text = "Distribuição de rendimentos de R$ 0,85 por cota do HGLG11."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        self.assertIn(claims[0].claim_type, ("DIVIDEND", "JCP"))

    def test_jcp_detectado(self):
        text = "Aprovado o pagamento de JCP no valor de R$ 0,25 por ação."
        claims = self._claims(text)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_type, "JCP")
        self.assertEqual(claims[0].numeric_value, Decimal("0.25"))

    def test_juros_sobre_capital_proprio(self):
        text = "A empresa declara juros sobre o capital próprio no valor de R$ 0,12 por ação preferencial."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_type, "JCP")

    def test_sem_falso_positivo(self):
        text = "Relatório de sustentabilidade sem valores financeiros."
        claims = self._claims(text)
        self.assertEqual(len(claims), 0)

    def test_valor_zero_ignorado(self):
        text = "Dividendos de R$ 0,00 por ação serão pagos."
        claims = self._claims(text)
        # Zero valor não deve gerar claim
        self.assertEqual(len(claims), 0)


class TestBuybackExtraction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = _builder(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _claims(self, text: str):
        return self.b._extract_claims("DOC2", "004170", "PETR4", "Recompra", text)

    def test_programa_de_recompra(self):
        text = "Aprovado o programa de recompra de até 10.000.000 de ações ordinárias."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(c.claim_type, "SHARE_BUYBACK")
        self.assertEqual(c.numeric_value, Decimal("10000000"))

    def test_recomprar_acoes(self):
        text = "O CA autorizou recomprar até 5.000.000 de ações para manutenção em tesouraria."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_type, "SHARE_BUYBACK")


class TestCapitalIncreaseExtraction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = _builder(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _claims(self, text: str):
        return self.b._extract_claims("DOC3", "004170", "PETR4", "Capital", text)

    def test_aumento_capital(self):
        text = "Homologado aumento do capital social no valor de R$ 167.698.667,00."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(c.claim_type, "CAPITAL_INCREASE")
        self.assertGreater(c.numeric_value, 0)


class TestDebtIssuanceExtraction(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = _builder(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _claims(self, text: str):
        return self.b._extract_claims("DOC4", "004170", "PETR4", "Dívida", text)

    def test_emissao_debentures(self):
        text = "A companhia aprovou emissão de debêntures no valor total de R$ 500.000.000,00."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(c.claim_type, "DEBT_ISSUANCE")
        self.assertEqual(c.numeric_value, Decimal("500000000.00"))

    def test_emissao_notas_comerciais(self):
        text = "Aprovada a 5ª emissão de notas comerciais no valor de R$ 100.000.000,00."
        claims = self._claims(text)
        self.assertGreaterEqual(len(claims), 1)
        self.assertEqual(claims[0].claim_type, "DEBT_ISSUANCE")


if __name__ == "__main__":
    unittest.main()
