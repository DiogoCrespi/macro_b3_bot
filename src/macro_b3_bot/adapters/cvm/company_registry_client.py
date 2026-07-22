from __future__ import annotations

import csv
import io
import httpx
from datetime import datetime, date, timezone
from pathlib import Path
from typing import List, Dict, Any

from macro_b3_bot.domain.cvm_models import CvmCompany
from macro_b3_bot.adapters.bcb.normalizer import record_checksum

class CvmCompanyRegistryClient:
    """
    Cliente para download e parsing do CSV oficial de Informações Cadastrais das Companhias Abertas da CVM.
    URL Oficial: https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/dados/cad_cia_aberta.csv
    """
    def __init__(self, raw_cache_dir: Path | None = None, timeout_seconds: float = 30.0):
        self.raw_cache_dir = raw_cache_dir
        self.timeout_seconds = timeout_seconds
        self.url = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/dados/cad_cia_aberta.csv"

    async def fetch_registry(self, ingestion_run_id: str) -> List[CvmCompany]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(self.url)
            resp.raise_for_status()
            raw_bytes = resp.content

        if self.raw_cache_dir:
            self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            cache_file = self.raw_cache_dir / f"{timestamp}_cad_cia_aberta.csv"
            cache_file.write_bytes(raw_bytes)

        # Trata encoding latin-1 / iso-8859-1 do padrão CVM
        text_data = raw_bytes.decode("iso-8859-1", errors="ignore")
        reader = csv.DictReader(io.StringIO(text_data), delimiter=";")

        companies: List[CvmCompany] = []
        observed_now = datetime.now(timezone.utc)

        for row in reader:
            cvm_code = str(row.get("CD_CVM", "")).strip()
            cnpj = str(row.get("CNPJ_CIA", "")).strip()
            legal_name = str(row.get("DENOM_SOCIAL", "")).strip()
            trading_name = str(row.get("DENOM_COMERC", "")).strip() or None

            if not cvm_code or not cnpj:
                continue

            reg_status = str(row.get("SIT", "")).strip()
            category = str(row.get("TP_MERC", "")).strip() or None

            reg_date = None
            reg_date_str = str(row.get("DT_REG", "")).strip()
            if reg_date_str:
                try:
                    reg_date = datetime.strptime(reg_date_str, "%Y-%m-%d").date()
                except ValueError:
                    pass

                rec_hash = record_checksum({
                    "cvm_code": cvm_code,
                    "cnpj": cnpj,
                    "legal_name": legal_name,
                    "reg_status": reg_status
                })

            company = CvmCompany(
                cvm_code=cvm_code,
                cnpj=cnpj,
                legal_name=legal_name,
                trading_name=trading_name,
                registration_status=reg_status,
                registration_date=reg_date,
                category=category,
                collected_at=observed_now,
                record_checksum=rec_hash,
                ingestion_run_id=ingestion_run_id
            )
            companies.append(company)

        return companies
