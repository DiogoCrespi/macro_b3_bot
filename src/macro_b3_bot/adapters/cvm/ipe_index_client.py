from __future__ import annotations

import csv
import io
import json
import zipfile
import hashlib
import httpx
import unicodedata
from datetime import datetime, date, timezone
from pathlib import Path
from typing import List

from macro_b3_bot.domain.ipe_models import IpeDocumentIndex
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum, record_checksum

def build_document_id(
    cvm_code: str,
    protocol: str | None,
    delivery_date: date | datetime,
    category: str,
    doc_type: str | None,
    version: int
) -> str:
    """
    Gera um ID determinístico e estável para o documento CVM IPE.
    Independe de URL, timestamp da coleta ou caminho de arquivo.
    """
    deliv_str = delivery_date.strftime("%Y-%m-%d") if isinstance(delivery_date, (date, datetime)) else str(delivery_date)
    identity = {
        "cvm_code": str(cvm_code).strip(),
        "protocol": str(protocol).strip() if protocol else "",
        "delivery_date": deliv_str,
        "category": unicodedata.normalize("NFKC", str(category).strip()),
        "document_type": unicodedata.normalize("NFKC", str(doc_type).strip()) if doc_type else "",
        "version": int(version or 1),
    }

    canonical = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

class CvmIpeIndexClient:
    """
    Cliente determinístico para download e parsing do índice IPE da CVM.
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
                    cvm_code = str(row.get("Codigo_CVM") or row.get("CD_CVM") or row.get("CD_CIA") or "").strip()
                    company_name = str(row.get("Nome_Companhia") or row.get("DENOM_CIA") or row.get("DENOM_SOCIAL") or "").strip()
                    category_raw = str(row.get("Categoria") or row.get("CATEGORIA") or "Outros").strip()
                    category = unicodedata.normalize("NFKC", category_raw)
                    
                    doc_type_raw = str(row.get("Tipo") or row.get("TIPO") or "").strip() or None
                    doc_type = unicodedata.normalize("NFKC", doc_type_raw) if doc_type_raw else None
                    
                    subj_raw = str(row.get("Assunto") or row.get("ASSUNTO") or "").strip() or None
                    subject = unicodedata.normalize("NFKC", subj_raw) if subj_raw else None
                    dt_receb = str(row.get("Data_Entrega") or row.get("DT_RECEB") or row.get("DT_ENTREGA") or "").strip()

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
                    dt_refer = str(row.get("Data_Referencia") or row.get("DT_REFER") or "").strip()
                    if dt_refer:
                        try:
                            ref_date = datetime.strptime(dt_refer[:10], "%Y-%m-%d").date()
                        except ValueError:
                            pass

                    protocol = str(row.get("Protocolo_Entrega") or row.get("NUM_PROTOCOL_ENTREGA") or "").strip() or None
                    version = int(row.get("Versao") or row.get("VERSAO") or "1" or "1")
                    source_url = str(row.get("Link_Download") or row.get("LINK_DOWNLOAD") or row.get("URL") or "").strip() or None

                    doc_id = build_document_id(
                        cvm_code=cvm_code,
                        protocol=protocol,
                        delivery_date=delivery_date,
                        category=category,
                        doc_type=doc_type,
                        version=version
                    )

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
