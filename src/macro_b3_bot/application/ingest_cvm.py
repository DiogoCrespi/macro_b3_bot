from __future__ import annotations

import uuid
import yaml
from pathlib import Path
from typing import Dict, Any, List

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.cvm.company_registry_client import CvmCompanyRegistryClient
from macro_b3_bot.adapters.cvm.zip_reader import CvmZipReader
from macro_b3_bot.adapters.b3_screener import B3ScreenerJsonBridge
from macro_b3_bot.infrastructure.store import DatabaseStore

class CvmIngestionPipeline:
    """
    Orquestrador de ingestão do Cadastro de Cias Abertas e Demonstrações Financeiras ITR/DFP da CVM.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.raw_dir = settings.data_dir / "raw" / "cvm"
        self.db_path = settings.data_dir / "audit.duckdb"
        self.registry_client = CvmCompanyRegistryClient(raw_cache_dir=self.raw_dir / "registry")
        self.zip_reader = CvmZipReader(raw_cache_dir=self.raw_dir / "statements")

    async def ingest_registry(self) -> Dict[str, Any]:
        run_id = f"RUN_CVM_CAD_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, "CVM_REGISTRY")

        companies = await self.registry_client.fetch_registry(ingestion_run_id=run_id)
        inserted = 0
        duplicated = 0

        for comp in companies:
            was_inserted = store.save_cvm_company(comp.model_dump(mode="json"))
            if was_inserted:
                inserted += 1
            else:
                duplicated += 1

        # Mapeia vínculos b3_screener.ticker <-> CVM CNPJ
        mapped_count = self._map_tickers_to_cvm(store)

        store.finish_ingestion_run(run_id, "SUCCESS", len(companies), inserted, 0)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "received": len(companies),
            "inserted": inserted,
            "duplicated": duplicated,
            "mapped_tickers": mapped_count
        }

    def _map_tickers_to_cvm(self, store: DatabaseStore) -> int:
        export_path = self.settings.b3_screener_export
        if not export_path.exists():
            return 0

        bridge = B3ScreenerJsonBridge(export_path)
        assets = bridge.load_assets()
        mapped = 0

        for asset in assets:
            cnpj_raw = asset.metrics.get("cnpj") or asset.metrics.get("cnpj_cia")
            if cnpj_raw:
                cnpj_clean = str(cnpj_raw).replace(".", "").replace("/", "").replace("-", "").strip().zfill(14)
                res = store.connection.execute(
                    "SELECT cvm_code FROM cvm_companies WHERE REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = ?", [cnpj_clean]
                ).fetchone()
                cvm_code = res[0] if res else None
                
                store.save_ticker_mapping({
                    "ticker": asset.ticker,
                    "cvm_code": cvm_code,
                    "cnpj": cnpj_clean,
                    "mapping_source": "EXACT_CNPJ",
                    "confidence": 1.0 if cvm_code else 0.5,
                    "validated": True if cvm_code else False
                })
                if cvm_code:
                    mapped += 1

        return mapped

    async def ingest_statements(self, doc_type: str, years: List[int]) -> Dict[str, Any]:
        run_id = f"RUN_CVM_{doc_type.upper()}_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, f"CVM_{doc_type.upper()}")

        total_docs = 0
        total_lines = 0
        total_inserted = 0

        for year in years:
            docs, lines = await self.zip_reader.fetch_and_parse_statements(
                doc_type=doc_type, year=year, ingestion_run_id=run_id
            )
            total_docs += len(docs)
            total_lines += len(lines)

            for doc in docs:
                store.save_cvm_document(doc.model_dump(mode="json"))

            for line in lines:
                was_inserted = store.save_financial_line(line.model_dump(mode="json"))
                if was_inserted:
                    total_inserted += 1

        store.finish_ingestion_run(run_id, "SUCCESS", total_lines, total_inserted, 0)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "doc_type": doc_type.upper(),
            "years": years,
            "documents_count": total_docs,
            "statement_lines_received": total_lines,
            "statement_lines_inserted": total_inserted
        }
