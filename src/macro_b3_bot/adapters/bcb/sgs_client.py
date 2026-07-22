from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List

from macro_b3_bot.domain.macro_models import MacroObservation
from .http_client import BcbHttpClient
from .normalizer import parse_decimal, record_checksum, split_date_range

class BcbSgsClient:
    """
    Cliente para coleta de séries temporais históricas do BCB SGS com checksum por registro individual.
    """
    def __init__(self, raw_cache_dir: Path | None = None):
        self.http_client = BcbHttpClient(raw_cache_dir=raw_cache_dir)

    async def fetch_series(
        self,
        code: str,
        name: str,
        unit: str,
        frequency: str,
        start_date: date,
        end_date: date,
        ingestion_run_id: str
    ) -> List[MacroObservation]:
        
        date_chunks = split_date_range(start_date, end_date, max_years=5)
        observations: List[MacroObservation] = []
        observed_now = datetime.now(timezone.utc)

        for chunk_start, chunk_end in date_chunks:
            start_str = chunk_start.strftime("%d/%m/%Y")
            end_str = chunk_end.strftime("%d/%m/%Y")
            url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json&dataInicial={start_str}&dataFinal={end_str}"

            raw_json, doc_checksum, _ = await self.http_client.get_json(url)

            if not isinstance(raw_json, list):
                continue

            for item in raw_json:
                data_str = item.get("data")
                valor_str = item.get("valor")

                if not data_str or valor_str is None:
                    continue

                try:
                    ref_date = datetime.strptime(data_str, "%d/%m/%Y").date()
                    value_dec = parse_decimal(valor_str)
                except ValueError:
                    continue

                rec_dict = {
                    "series_code": str(code),
                    "reference_date": str(ref_date),
                    "value": str(value_dec)
                }
                rec_hash = record_checksum(rec_dict)

                obs = MacroObservation(
                    source="BCB_SGS",
                    series_code=str(code),
                    indicator=name,
                    reference_date=ref_date,
                    observed_at=observed_now,
                    available_at=observed_now,
                    value=value_dec,
                    unit=unit,
                    frequency=frequency,
                    revision=0,
                    raw_checksum=rec_hash,
                    ingestion_run_id=ingestion_run_id
                )
                observations.append(obs)

        return observations
