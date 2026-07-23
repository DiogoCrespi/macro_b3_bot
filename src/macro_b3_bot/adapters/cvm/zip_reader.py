from __future__ import annotations

import csv
import io
import zipfile
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Tuple, Dict

from macro_b3_bot.domain.cvm_models import CvmDocument, FinancialStatementLine
from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum, record_checksum, parse_decimal

class CvmZipReader:
    """
    Leitor e parser de arquivos ZIP de ITR e DFP publicados pela CVM.
    URL ITR: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{year}.zip
    URL DFP: https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{year}.zip
    """
    def __init__(self, raw_cache_dir: Path | None = None, timeout_seconds: float = 60.0):
        self.raw_cache_dir = raw_cache_dir
        self.timeout_seconds = timeout_seconds

    async def fetch_and_parse_statements(
        self,
        doc_type: str, # "ITR" ou "DFP"
        year: int,
        ingestion_run_id: str,
        cvm_codes: set[str] | None = None,
    ) -> Tuple[List[CvmDocument], List[FinancialStatementLine]]:

        url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{doc_type.upper()}/DADOS/{doc_type.lower()}_cia_aberta_{year}.zip"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw_bytes = resp.content
        last_modified = resp.headers.get("Last-Modified")
        resource_available_at = (
            parsedate_to_datetime(last_modified).astimezone(timezone.utc)
            if last_modified
            else datetime.now(timezone.utc)
        )

        zip_checksum = compute_raw_checksum(raw_bytes)

        if self.raw_cache_dir:
            self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.raw_cache_dir / f"{doc_type.lower()}_{year}_{zip_checksum[:12]}.zip"
            cache_file.write_bytes(raw_bytes)

        documents_map: Dict[str, CvmDocument] = {}
        statement_lines: List[FinancialStatementLine] = []
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            for filename in zf.namelist():
                if not filename.endswith(".csv"):
                    continue
                statement_type = self._statement_type_from_filename(filename)
                if statement_type is None:
                    continue

                # Extrai tipo de demonstração (ex: DRE_con, BPA_con, DRE_ind)
                file_bytes = zf.read(filename)
                text_data = file_bytes.decode("iso-8859-1", errors="ignore")
                reader = csv.DictReader(io.StringIO(text_data), delimiter=";")

                for row in reader:
                    cvm_code = self._normalize_cvm_code(row.get("CD_CVM", ""))
                    cnpj = str(row.get("CNPJ_CIA", "")).strip()
                    dt_refer = str(row.get("DT_REFER", "")).strip()

                    if not cvm_code or not dt_refer:
                        continue
                    if cvm_codes is not None and cvm_code not in cvm_codes:
                        continue

                    try:
                        ref_date = datetime.strptime(dt_refer, "%Y-%m-%d").date()
                    except ValueError:
                        continue

                    version = int(row.get("VERSAO", "1") or "1")
                    doc_id = f"{doc_type.upper()}_{cvm_code}_{dt_refer}_v{version}"

                    if doc_id not in documents_map:
                        documents_map[doc_id] = CvmDocument(
                            document_id=doc_id,
                            document_type=doc_type.upper(),
                            cvm_code=cvm_code,
                            cnpj=cnpj,
                            reference_date=ref_date,
                            received_at=resource_available_at,
                            version=version,
                            raw_zip_checksum=zip_checksum,
                            ingestion_run_id=ingestion_run_id,
                            availability_basis="RESOURCE_LAST_MODIFIED",
                            source_url=url,
                        )

                    account_code = str(row.get("CD_CONTA", "")).strip()
                    account_desc = str(row.get("DS_CONTA", "")).strip()
                    val_str = row.get("VL_CONTA")

                    if not account_code or val_str is None:
                        continue

                    try:
                        val_dec = parse_decimal(val_str)
                    except ValueError:
                        continue

                    scope = "CONSOLIDATED" if ("CON" in filename.upper() or row.get("GRUPO_DFP", "").endswith("CON")) else "INDIVIDUAL"
                    start_date = None
                    dt_ini = str(row.get("DT_INI_EXERC", "")).strip()
                    if dt_ini:
                        try:
                            start_date = datetime.strptime(dt_ini, "%Y-%m-%d").date()
                        except ValueError:
                            pass

                    rec_hash = record_checksum({
                        "doc_id": doc_id,
                        "stmt_type": statement_type,
                        "scope": scope,
                        "account_code": account_code,
                        "value": str(val_dec)
                    })

                    raw_scale = str(row.get("ESCALA_MOEDA", "1") or "1").strip().upper()
                    if raw_scale == "MIL":
                        scale_val = 1000
                    elif raw_scale == "UNIDADE":
                        scale_val = 1
                    else:
                        try:
                            scale_val = int(raw_scale)
                        except ValueError:
                            scale_val = 1

                    line = FinancialStatementLine(
                        document_id=doc_id,
                        statement_type=statement_type,
                        scope=scope,
                        fiscal_order=str(row.get("ORDEM_EXERC", "ÚLTIMO")),
                        account_code=account_code,
                        account_description=account_desc,
                        value=val_dec,
                        currency="BRL",
                        scale=scale_val,
                        start_date=start_date,
                        end_date=ref_date,
                        record_checksum=rec_hash
                    )
                    statement_lines.append(line)

        return list(documents_map.values()), statement_lines

    @staticmethod
    def _statement_type_from_filename(filename: str) -> str | None:
        upper = filename.upper()
        for statement_type in ("BPA", "BPP", "DRE", "DRA", "DFC_MD", "DFC_MI", "DMPL", "DVA"):
            if statement_type in upper:
                return statement_type.replace("_", "-")
        return None

    @staticmethod
    def _normalize_cvm_code(value: object) -> str:
        raw = str(value).strip()
        return raw.lstrip("0") or "0"
