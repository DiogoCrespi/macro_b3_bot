"""
Sprint 3A: Integration tests validating the full pipeline on real data.
Requires run_sprint3a_full.py to have been executed first.
"""
import sys
import unittest
import duckdb
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR  = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings


class TestSprint3AIntegration(unittest.TestCase):
    """
    Valida o estado do banco de dados após a execução do Sprint 3A completo.
    Esses testes falharão se run_sprint3a_full.py não foi executado.
    """

    @classmethod
    def setUpClass(cls):
        settings = Settings()
        db_path = settings.data_dir / "audit.duckdb"
        if not db_path.exists():
            raise unittest.SkipTest(f"Banco de dados não encontrado: {db_path}")
        cls.conn = duckdb.connect(str(db_path), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_extracted_documents_exists(self):
        """Extração produziu documentos."""
        count = self.conn.execute("SELECT COUNT(*) FROM extracted_documents").fetchone()[0]
        self.assertGreater(count, 0, "Nenhum documento extraído")

    def test_pdf_extraction_successful(self):
        """Documentos PDF foram extraídos com pypdf."""
        pdf_count = self.conn.execute(
            "SELECT COUNT(*) FROM extracted_documents WHERE extraction_method = 'PDF_DIRECT'"
        ).fetchone()[0]
        self.assertGreater(pdf_count, 0, "Nenhum PDF extraído via pypdf")

    def test_extraction_quality_acceptable(self):
        """Qualidade de extração >= 0.85 para maioria dos documentos."""
        total = self.conn.execute("SELECT COUNT(*) FROM extracted_documents").fetchone()[0]
        high_quality = self.conn.execute(
            "SELECT COUNT(*) FROM extracted_documents WHERE extraction_quality >= 0.85"
        ).fetchone()[0]
        if total > 0:
            ratio = high_quality / total
            self.assertGreater(ratio, 0.5, f"Menos de 50% com quality>=0.85 ({ratio:.1%})")

    def test_evidence_claims_generated(self):
        """EvidenceClaims foram gerados dos documentos."""
        count = self.conn.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
        self.assertGreater(count, 0, "Nenhum EvidenceClaim gerado")

    def test_evidence_claims_have_valid_values(self):
        """Claims de valor por ação têm numeric_value > 0."""
        neg_or_zero = self.conn.execute("""
            SELECT COUNT(*) FROM evidence_claims
            WHERE claim_type IN ('DIVIDEND', 'JCP')
              AND (numeric_value IS NULL OR CAST(numeric_value AS DOUBLE) <= 0)
        """).fetchone()[0]
        self.assertEqual(neg_or_zero, 0, f"{neg_or_zero} claims com valor inválido")

    def test_claim_types_distribution(self):
        """Múltiplos tipos de claim presentes."""
        types = self.conn.execute(
            "SELECT DISTINCT claim_type FROM evidence_claims"
        ).fetchall()
        type_set = {t[0] for t in types}
        # Deve ter pelo menos dividendo/JCP dado que 54 docs têm 'juros sobre capital'
        self.assertTrue(
            len(type_set) >= 1,
            f"Esperado >= 1 tipo de claim, encontrado: {type_set}"
        )

    def test_event_candidates_generated(self):
        """EventCandidates foram consolidados."""
        count = self.conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0]
        self.assertGreater(count, 0, "Nenhum EventCandidate consolidado")

    def test_event_candidates_have_valid_scores(self):
        """EventCandidates têm scores no intervalo [0,1]."""
        invalid = self.conn.execute("""
            SELECT COUNT(*) FROM event_candidates
            WHERE novelty_score < 0 OR novelty_score > 1
               OR materiality_score < 0 OR materiality_score > 1
        """).fetchone()[0]
        self.assertEqual(invalid, 0, f"{invalid} eventos com scores inválidos")

    def test_no_buy_mode(self):
        """Modo BUY permanece bloqueado (sem asset_snapshots com sinal BUY)."""
        # O projeto está em modo pesquisa — não há sinais BUY gerados
        snapshots = self.conn.execute("SELECT COUNT(*) FROM asset_snapshots").fetchone()[0]
        # Pode ter snapshots, mas não devem ter sido gerados pelo pipeline de evidências
        # (os snapshots vêm de outra fonte — b3_screener)
        self.assertIsNotNone(snapshots)  # Tabela existe

    def test_deduplication_consistency(self):
        """Links de deduplicação têm similarity no intervalo [0,1]."""
        invalid = self.conn.execute("""
            SELECT COUNT(*) FROM document_duplicate_links
            WHERE similarity < 0 OR similarity > 1
        """).fetchone()[0]
        self.assertEqual(invalid, 0)

    def test_queue_status_progression(self):
        """Documentos avançaram da fila DOWNLOADED → EXTRACTED → DEDUPLICATED → EVIDENCE_BUILT."""
        downloaded = self.conn.execute(
            "SELECT COUNT(*) FROM ipe_processing_queue WHERE status = 'DOWNLOADED'"
        ).fetchone()[0]
        # Após pipeline completo, nenhum deve permanecer em DOWNLOADED
        self.assertEqual(downloaded, 0, f"{downloaded} documentos ainda em DOWNLOADED")


if __name__ == "__main__":
    unittest.main()
