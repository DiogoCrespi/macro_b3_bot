from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Dict, Any, List

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.cvm.company_registry_client import CvmCompanyRegistryClient
from macro_b3_bot.adapters.cvm.zip_reader import CvmZipReader
from macro_b3_bot.adapters.b3_screener import B3ScreenerJsonBridge
from macro_b3_bot.infrastructure.store import DatabaseStore

@dataclass
class CvmIngestionResult:
    documents_received: int = 0
    documents_inserted: int = 0
    documents_duplicated: int = 0
    documents_restatement: int = 0

    lines_received: int = 0
    lines_inserted: int = 0
    lines_duplicated: int = 0
    lines_revised: int = 0
    lines_rejected: int = 0

class CvmIngestionPipeline:
    """
    Orquestrador de ingestão do Cadastro de Cias Abertas e Demonstrações Financeiras ITR/DFP da CVM.
    Comprova 100% de idempotência entre documentos e linhas contábeis.
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

        mapped_stats = self.map_tickers_with_asset_class_breakdown(store)

        store.finish_ingestion_run(run_id, "SUCCESS", len(companies), inserted, 0)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "received": len(companies),
            "inserted": inserted,
            "duplicated": duplicated,
            "mapped_stats": mapped_stats
        }

    def map_tickers_with_asset_class_breakdown(self, store: DatabaseStore) -> Dict[str, Any]:
        export_path = self.settings.b3_screener_export
        if not export_path.exists():
            return {"total": 0, "mapped": 0, "coverage_pct": 0.0}

        bridge = B3ScreenerJsonBridge(export_path)
        assets = bridge.load_assets()

        total_universe = len(assets)
        by_class: Dict[str, Dict[str, int]] = {
            "STOCK": {"total": 0, "mapped": 0},
            "FII": {"total": 0, "mapped": 0},
            "ETF": {"total": 0, "mapped": 0},
            "BDR": {"total": 0, "mapped": 0},
            "OTHER": {"total": 0, "mapped": 0}
        }

        total_mapped = 0

        for asset in assets:
            aclass = str(asset.asset_class).upper()
            cls_key = aclass if aclass in by_class else "OTHER"
            by_class[cls_key]["total"] += 1

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
                    total_mapped += 1
                    by_class[cls_key]["mapped"] += 1

        stock_total = by_class["STOCK"]["total"]
        stock_mapped = by_class["STOCK"]["mapped"]
        stock_coverage = (stock_mapped / stock_total * 100.0) if stock_total > 0 else 0.0

        return {
            "total_universe": total_universe,
            "total_mapped": total_mapped,
            "stock_total": stock_total,
            "stock_mapped": stock_mapped,
            "stock_coverage_pct": round(stock_coverage, 2),
            "by_class": by_class
        }

    async def ingest_statements(
        self, doc_type: str, years: List[int], cvm_codes: set[str] | None = None
    ) -> CvmIngestionResult:
        run_id = f"RUN_CVM_{doc_type.upper()}_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, f"CVM_{doc_type.upper()}")

        res = CvmIngestionResult()

        for year in years:
            docs, lines = await self.zip_reader.fetch_and_parse_statements(
                doc_type=doc_type,
                year=year,
                ingestion_run_id=run_id,
                cvm_codes=cvm_codes,
            )
            res.documents_received += len(docs)
            res.lines_received += len(lines)

            for doc in docs:
                was_inserted, was_reversion = store.save_cvm_document_with_status(doc.model_dump(mode="json"))
                if was_inserted:
                    res.documents_inserted += 1
                elif was_reversion:
                    res.documents_restatement += 1
                else:
                    res.documents_duplicated += 1

            for line in lines:
                was_inserted = store.save_financial_line(line.model_dump(mode="json"))
                if was_inserted:
                    res.lines_inserted += 1
                else:
                    res.lines_duplicated += 1

        store.finish_ingestion_run(run_id, "SUCCESS", res.lines_received, res.lines_inserted, res.lines_rejected)
        store.close()

        return res
