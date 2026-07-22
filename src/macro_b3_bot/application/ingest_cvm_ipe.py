from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Any, List

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.cvm.ipe_index_client import CvmIpeIndexClient
from macro_b3_bot.infrastructure.store import DatabaseStore

class CvmIpeIngestionPipeline:
    """
    Orquestrador de ingestão do índice de metadados de documentos IPE da CVM (anos 2025 e 2026).
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.raw_dir = settings.data_dir / "raw" / "cvm" / "ipe"
        self.db_path = settings.data_dir / "audit.duckdb"
        self.client = CvmIpeIndexClient(raw_cache_dir=self.raw_dir)

    async def ingest_ipe_index(self, years: List[int]) -> Dict[str, Any]:
        run_id = f"RUN_CVM_IPE_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, "CVM_IPE_INDEX")

        total_received = 0
        total_inserted = 0
        total_duplicated = 0

        for year in years:
            docs = await self.client.fetch_ipe_index(year=year, ingestion_run_id=run_id)
            total_received += len(docs)

            for doc in docs:
                was_inserted = store.save_ipe_document_index(doc.model_dump(mode="json"))
                if was_inserted:
                    total_inserted += 1
                else:
                    total_duplicated += 1

        store.finish_ingestion_run(run_id, "SUCCESS", total_received, total_inserted, 0)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "years": years,
            "received": total_received,
            "inserted": total_inserted,
            "duplicated": total_duplicated
        }
