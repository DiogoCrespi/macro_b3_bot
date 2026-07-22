import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

def run_audit(db_path: Path | None = None) -> bool:
    print("\n--------------------------------------------------------------------------")
    print(" 🔍 EXECUCAO DA AUDITORIA RIGOROSA DE INTEGRIDADE DA CVM (macro-b3 audit-cvm)")
    print("--------------------------------------------------------------------------\n")
    
    if db_path is None:
        settings = Settings()
        db_path = settings.data_dir / "audit.duckdb"

    if not db_path.exists():
        print(f"❌ Banco {db_path} não encontrado!")
        return False

    store = DatabaseStore(db_path)
    conn = store.connection

    # 1. Documentos duplicados logicamente
    dup_docs = conn.execute("""
        SELECT document_type, cvm_code, reference_date, version, COUNT(*) AS occurrences
        FROM cvm_documents
        GROUP BY document_type, cvm_code, reference_date, version
        HAVING COUNT(*) > 1;
    """).fetchall()

    # 2. Linhas órfãs
    orphan_res = conn.execute("""
        SELECT COUNT(*) AS orphan_lines
        FROM financial_statement_lines f
        LEFT JOIN cvm_documents d ON f.document_id = d.document_id
        WHERE d.document_id IS NULL;
    """).fetchone()[0]

    # 3. Duplicação contábil lógica
    dup_lines = conn.execute("""
        SELECT document_id, statement_type, scope, fiscal_order, account_code, start_date, end_date, COUNT(*) AS versions
        FROM financial_statement_lines
        GROUP BY document_id, statement_type, scope, fiscal_order, account_code, start_date, end_date
        HAVING COUNT(*) > 1;
    """).fetchall()

    # 4. Totais
    total_companies = store.count_cvm_companies()
    total_mappings = store.count_ticker_mappings()
    total_docs = store.count_cvm_documents()
    total_lines = store.count_financial_lines()

    print("==========================================================================")
    print("   RELATORIO DE AUDITORIA E INTEGRIDADE CVM (audit-cvm)")
    print("==========================================================================")
    print(f"Companhias CVM cadastradas:    {total_companies:,}")
    print(f"Mapeamentos B3 ↔ CVM:           {total_mappings:,}")
    print(f"Documentos ITR/DFP:             {total_docs:,}")
    print(f"Linhas contábeis totais:        {total_lines:,}")
    print("--------------------------------------------------------------------------")
    print(f"Documentos duplicados logicamente: {len(dup_docs)} (Esperado: 0)")
    print(f"Linhas contábeis órfãs:            {orphan_res} (Esperado: 0)")
    print(f"Duplicação contábil lógica:         {len(dup_lines)} (Esperado: 0)")
    print("--------------------------------------------------------------------------")
    
    passed = (len(dup_docs) == 0 and orphan_res == 0 and len(dup_lines) == 0)

    if passed:
        print(" VEREDITO DE AUDITORIA: 100% APROVADO (Zero anomalias de integridade)")
    else:
        print("❌ VEREDITO DE AUDITORIA: ANOMALIAS DETECTADAS!")
    print("==========================================================================\n")

    store.close()
    return passed

if __name__ == "__main__":
    success = run_audit()
    sys.exit(0 if success else 1)
