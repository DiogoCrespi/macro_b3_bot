import sys
import unittest
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.infrastructure.store import DatabaseStore

class TestCvmAuditSuite(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_queries_on_empty_db(self):
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Documentos duplicados logicamente
        dup_docs = conn.execute("""
            SELECT document_type, cvm_code, reference_date, version, COUNT(*) AS occurrences
            FROM cvm_documents
            GROUP BY document_type, cvm_code, reference_date, version
            HAVING COUNT(*) > 1;
        """).fetchall()
        self.assertEqual(len(dup_docs), 0)

        # Linhas órfãs
        orphan_lines = conn.execute("""
            SELECT COUNT(*) AS orphan_lines
            FROM financial_statement_lines f
            LEFT JOIN cvm_documents d ON f.document_id = d.document_id
            WHERE d.document_id IS NULL;
        """).fetchone()[0]
        self.assertEqual(orphan_lines, 0)

        # Duplicação contábil lógica
        dup_lines = conn.execute("""
            SELECT document_id, statement_type, scope, fiscal_order, account_code, start_date, end_date, COUNT(*) AS versions
            FROM financial_statement_lines
            GROUP BY document_id, statement_type, scope, fiscal_order, account_code, start_date, end_date
            HAVING COUNT(*) > 1;
        """).fetchall()
        self.assertEqual(len(dup_lines), 0)

        store.close()

    def test_audit_script_execution_verdict(self):
        from scripts.audit_cvm import run_audit
        store = DatabaseStore(self.db_path)
        store.close()
        # Valida que o script de auditoria executa sem exceção
        res = run_audit(db_path=self.db_path)
        self.assertTrue(res)

if __name__ == "__main__":
    unittest.main()
