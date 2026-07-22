from __future__ import annotations

import csv
import io
import zipfile
import httpx
from datetime import datetime, date, timezone
from pathlib import Path
from typing import List, Tuple

from macro_b3_bot.domain.ipe_models import IpeDocumentIndex
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum, record_checksum

class CvmIpeIndexClient:
    """
    Cliente para download e parsing dos metadados do índice de documentos IPE da CVM.
    URL Oficial: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip
    """
    def __init__(self, raw_cache_dir: Path | None = None, timeout_seconds: float = 60.0):
        self.raw_cache_dir = raw_cache_dir
        self.timeout_seconds = timeout_seconds

    async def fetch_ipe_index(self, year: int, ingestion_run_id: str) -> List[IpeDocumentIndex]:
        url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw_bytes = resp.content

        zip_checksum = compute_raw_checksum(raw_bytes)

        if self.raw_cache_dir:
            self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.raw_cache_dir / f"ipe_{year}_{zip_checksum[:12]}.zip"
            cache_file.write_bytes(raw_bytes)

        ipe_documents: List[IpeDocumentIndex] = []

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            for filename in zf.namelist():
                if not filename.endswith(".csv"):
                    continue

                file_bytes = zf.read(filename)
                text_data = file_bytes.decode("iso-8859-1", errors="ignore")
                reader = csv.DictReader(io.StringIO(text_data), delimiter=";")

                for row in reader:
                    cvm_code = str(row.get("CD_CVM") or row.get("Codigo_CVM") or row.get("CD_CIA") or "").strip()
                    company_name = str(row.get("DENOM_CIA") or row.get("Nome_Companhia") or row.get("DENOM_SOCIAL") or "").strip()
                    category = str(row.get("CATEGORIA") or row.get("Categoria") or "Outros").strip()
                    doc_type = str(row.get("TIPO") or row.get("Tipo") or "").strip() or None
                    subject = str(row.get("ASSUNTO") or row.get("Assunto") or "").strip() or None
                    dt_receb = str(row.get("DT_RECEB") or row.get("Data_Entrega") or row.get("DT_ENTREGA") or row.get("DT_RECEBIMENTO") or "").strip()

                    if not cvm_code and not dt_receb:
                        continue

                    try:
                        delivery_date = datetime.strptime(dt_receb, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        try:
                            delivery_date = datetime.strptime(dt_receb[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except ValueError:
                            delivery_date = datetime.now(timezone.utc)

                    ref_date = None
                    dt_refer = str(row.get("DT_REFER", "")).strip()
                    if dt_refer:
                        try:
                            ref_date = datetime.strptime(dt_refer[:10], "%Y-%m-%d").date()
                        except ValueError:
                            pass

                    protocol = str(row.get("NUM_PROTOCOL_ENTREGA", "")).strip() or None
                    version = int(row.get("VERSAO", "1") or "1")
                    source_url = str(row.get("LINK_DOWNLOAD", "")).strip() or None

                    doc_id = f"IPE_{cvm_code}_{protocol or delivery_date.strftime('%Y%m%d%H%M%S')}_v{version}"

                    rec_hash = record_checksum({
                        "doc_id": doc_id,
                        "cvm_code": cvm_code,
                        "category": category,
                        "subject": subject,
                        "delivery_date": str(delivery_date)
                    })

                    doc = IpeDocumentIndex(
                        document_id=doc_id,
                        cvm_code=cvm_code,
                        company_name=company_name,
                        category=category,
                        document_type=doc_type,
                        subject=subject,
                        reference_date=ref_date,
                        delivery_date=delivery_date,
                        protocol=protocol,
                        version=version,
                        source_url=source_url,
                        raw_index_checksum=zip_checksum,
                        record_checksum=rec_hash,
                        ingestion_run_id=ingestion_run_id
                    )
                    ipe_documents.append(doc)

        return ipe_documents
