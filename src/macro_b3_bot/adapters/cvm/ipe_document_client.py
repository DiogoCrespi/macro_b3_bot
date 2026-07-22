from __future__ import annotations

import asyncio
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Optional

from macro_b3_bot.domain.document_models import DownloadedDocument
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum

class IpeDocumentDownloader:
    """
    Cliente de download seguro e imutável para documentos do IPE CVM.
    Aplica limites de concorrência, tamanho máximo (25 MB), timeouts e validação de URL.
    """
    def __init__(
        self,
        storage_base_dir: Path,
        max_concurrency: int = 4,
        timeout_seconds: float = 30.0,
        max_file_size_bytes: int = 25 * 1024 * 1024
    ):
        self.storage_base_dir = storage_base_dir
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.timeout_seconds = timeout_seconds
        self.max_file_size_bytes = max_file_size_bytes

    async def download_document(
        self,
        document_id: str,
        source_url: str,
        cvm_code: str,
        year: int,
        ingestion_run_id: str
    ) -> Optional[DownloadedDocument]:

        # Validação básica de URL (apenas HTTP/HTTPS)
        if not source_url.startswith("http://") and not source_url.startswith("https://"):
            return None

        async with self.semaphore:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    resp = await client.get(source_url)
                    
                    if resp.status_code != 200:
                        return None

                    content_bytes = resp.content
                    if len(content_bytes) > self.max_file_size_bytes:
                        return None

                    doc_checksum = compute_raw_checksum(content_bytes)
                    mime_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()

                    ext = "pdf" if "pdf" in mime_type else "html" if "html" in mime_type else "txt"
                    
                    raw_dir = self.storage_base_dir / str(year) / str(cvm_code) / str(document_id)
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    file_path = raw_dir / f"{doc_checksum[:16]}.{ext}"
                    file_path.write_bytes(content_bytes)

                    now = datetime.now(timezone.utc)

                    return DownloadedDocument(
                        document_id=document_id,
                        source_url=source_url,
                        http_status=resp.status_code,
                        mime_type=mime_type,
                        file_extension=ext,
                        file_size_bytes=len(content_bytes),
                        raw_path=str(file_path),
                        document_checksum=doc_checksum,
                        downloaded_at=now,
                        ingestion_run_id=ingestion_run_id
                    )
            except Exception:
                return None
