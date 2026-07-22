import sys
import unittest
import tempfile
from pathlib import Path
from datetime import date
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.domain.cvm_models import FinancialStatementLine
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestCvmStatements(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_financial_line_persistence_and_deduplication(self):
        store = DatabaseStore(self.db_path)
        line1 = FinancialStatementLine(
            document_id="DFP_004170_2025-12-31_v1",
            statement_type="DRE",
            scope="CONSOLIDATED",
            fiscal_order="ÚLTIMO",
            account_code="3.01",
            account_description="Receita de Venda de Bens e/ou Serviços",
            value=Decimal("500000000.00"),
            currency="BRL",
            scale=1000,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            record_checksum="line_hash_1"
        )
        was_inserted = store.save_financial_line(line1.model_dump(mode="json"))
        self.assertTrue(was_inserted)
        self.assertEqual(store.count_financial_lines(), 1)

        # Tentativa de inserir duplicado idêntico (deve retornar False)
        was_dup = store.save_financial_line(line1.model_dump(mode="json"))
        self.assertFalse(was_dup)
        self.assertEqual(store.count_financial_lines(), 1)

        store.close()

    def test_consolidated_vs_individual_separation(self):
        store = DatabaseStore(self.db_path)
        con_line = FinancialStatementLine(
            document_id="ITR_004170_2026-03-31_v1",
            statement_type="DRE",
            scope="CONSOLIDATED",
            fiscal_order="ÚLTIMO",
            account_code="3.01",
            account_description="Receita Consolidada",
            value=Decimal("100.0"),
            currency="BRL",
            scale=1,
            end_date=date(2026, 3, 31),
            record_checksum="hash_con"
        )
        ind_line = FinancialStatementLine(
            document_id="ITR_004170_2026-03-31_v1",
            statement_type="DRE",
            scope="INDIVIDUAL",
            fiscal_order="ÚLTIMO",
            account_code="3.01",
            account_description="Receita Individual",
            value=Decimal("80.0"),
            currency="BRL",
            scale=1,
            end_date=date(2026, 3, 31),
            record_checksum="hash_ind"
        )
        store.save_financial_line(con_line.model_dump(mode="json"))
        store.save_financial_line(ind_line.model_dump(mode="json"))
        
        self.assertEqual(store.count_financial_lines(), 2)
        store.close()

if __name__ == "__main__":
    unittest.main()
