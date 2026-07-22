from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List

from macro_b3_bot.domain.macro_models import MarketExpectation
from .http_client import BcbHttpClient
from .normalizer import parse_decimal

class BcbExpectationsClient:
    """
    Cliente para consulta das Expectativas de Mercado (BCB Focus) via API OData oficial.
    """
    def __init__(self, raw_cache_dir: Path | None = None):
        self.http_client = BcbHttpClient(raw_cache_dir=raw_cache_dir)

    async def fetch_annual_expectations(
        self,
        indicator: str,
        since: date,
        ingestion_run_id: str
    ) -> List[MarketExpectation]:
        
        since_str = since.strftime("%Y-%m-%d")
        filter_query = f"Indicador eq '{indicator}' and Data ge '{since_str}'"
        encoded_filter = urllib.parse.quote(filter_query)
        
        url = f"https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais?$filter={encoded_filter}&$orderby=Data%20desc&$format=json&$top=100"

        raw_json, checksum, _ = await self.http_client.get_json(url)
        value_list = raw_json.get("value", []) if isinstance(raw_json, dict) else []

        expectations: List[MarketExpectation] = []
        observed_now = datetime.now(timezone.utc)

        for item in value_list:
            data_str = item.get("Data")
            target_period = str(item.get("DataReferencia", ""))
            
            if not data_str or not target_period:
                continue

            try:
                ref_date = datetime.strptime(data_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            stats_mapping = {
                "Mediana": item.get("Mediana"),
                "Media": item.get("Media"),
                "DesvioPadrao": item.get("DesvioPadrao"),
                "Minimo": item.get("Minimo"),
                "Maximo": item.get("Maximo")
            }

            for stat_name, stat_val in stats_mapping.items():
                if stat_val is None:
                    continue

                try:
                    val_dec = parse_decimal(stat_val)
                except ValueError:
                    continue

                exp = MarketExpectation(
                    source="BCB_FOCUS",
                    indicator=indicator,
                    reference_date=ref_date,
                    target_period=target_period,
                    statistic=stat_name,
                    value=val_dec,
                    base_calculation=item.get("baseCalculo"),
                    observed_at=observed_now,
                    raw_checksum=checksum,
                    ingestion_run_id=ingestion_run_id
                )
                expectations.append(exp)

        return expectations
