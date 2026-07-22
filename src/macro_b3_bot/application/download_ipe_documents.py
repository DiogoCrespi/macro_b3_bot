from __future__ import annotations

import uuid
from typing import Dict, Any

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.cvm.ipe_document_client import IpeDocumentDownloader
from macro_b3_bot.infrastructure.store import DatabaseStore

class IpeDownloadPipeline:
    """
    Orquestrador do download controlado e seguro de documentos IPE para o lote piloto (max 500).
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage_dir = settings.data_dir / "raw" / "cvm" / "ipe"
        self.db_path = settings.data_dir / "audit.duckdb"
        self.downloader = IpeDocumentDownloader(storage_base_dir=self.storage_dir)

    async def download_pilot_batch(self, limit: int = 500, min_priority: float = 0.65) -> Dict[str, Any]:
        run_id = f"RUN_CVM_DL_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Seleciona lote piloto com portões rígidos
        query_rows = conn.execute("""
            SELECT i.document_id, i.source_url, i.cvm_code, EXTRACT(YEAR FROM i.delivery_date) AS year
            FROM ipe_processing_queue q
            JOIN ipe_document_index i USING (document_id)
            WHERE q.status IN ('DISCOVERED', 'QUEUED')
              AND i.source_url IS NOT NULL
              AND i.source_url != ''
            ORDER BY q.priority_score DESC, i.delivery_date DESC
            LIMIT ?
        """, [limit]).fetchall()

        total_selected = len(query_rows)
        successful_downloads = 0
        failed_downloads = 0

        for row in query_rows:
            doc_id, source_url, cvm_code, year = row
            doc_obj = await self.downloader.download_document(
                document_id=doc_id,
                source_url=source_url,
                cvm_code=cvm_code,
                year=int(year or 2026),
                ingestion_run_id=run_id
            )

            if doc_obj:
                store.save_downloaded_document(doc_obj.model_dump(mode="json"))
                # Atualiza estado na fila
                conn.execute(
                    "UPDATE ipe_processing_queue SET status = 'DOWNLOADED', updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                    [doc_id]
                )
                successful_downloads += 1
            else:
                conn.execute(
                    "UPDATE ipe_processing_queue SET status = 'DOWNLOAD_FAILED', updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                    [doc_id]
                )
                failed_downloads += 1

        store.close()

        return {
            "run_id": run_id,
            "total_selected": total_selected,
            "successful_downloads": successful_downloads,
            "failed_downloads": failed_downloads
        }
