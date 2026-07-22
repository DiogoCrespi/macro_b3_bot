from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List

from macro_b3_bot.domain.macro_models import MarketExpectation
from .http_client import BcbHttpClient
from .normalizer import parse_decimal, record_checksum

class BcbExpectationsClient:
    """
    Cliente para consulta paginada das Expectativas de Mercado (BCB Focus) via API OData oficial.
    Suporta fatiamento de paginas via $top e $skip sem truncamento de dados.
    """
    def __init__(self, raw_cache_dir: Path | None = None, page_size: int = 1000):
        self.http_client = BcbHttpClient(raw_cache_dir=raw_cache_dir)
        self.page_size = page_size
        self.max_pages = 10000

    async def fetch_annual_expectations(
        self,
        indicator: str,
        since: date,
        ingestion_run_id: str
    ) -> List[MarketExpectation]:
        
        since_str = since.strftime("%Y-%m-%d")
        filter_query = f"Indicador eq '{indicator}' and Data ge '{since_str}'"
        encoded_filter = urllib.parse.quote(filter_query)
        base_url = f"https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais?$filter={encoded_filter}&$orderby=Data%20desc&$format=json"

        expectations: List[MarketExpectation] = []
        observed_now = datetime.now(timezone.utc)

        skip = 0
        pages_fetched = 0

        while True:
            url = f"{base_url}&$top={self.page_size}&$skip={skip}"
            raw_json, doc_checksum, _ = await self.http_client.get_json(url)
            page_items = raw_json.get("value", []) if isinstance(raw_json, dict) else []

            if not page_items:
                break

            for item in page_items:
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

                    rec_dict = {
                        "indicator": indicator,
                        "reference_date": str(ref_date),
                        "target_period": target_period,
                        "statistic": stat_name,
                        "value": str(val_dec)
                    }
                    rec_hash = record_checksum(rec_dict)

                    exp = MarketExpectation(
                        source="BCB_FOCUS",
                        indicator=indicator,
                        reference_date=ref_date,
                        target_period=target_period,
                        statistic=stat_name,
                        value=val_dec,
                        base_calculation=item.get("baseCalculo"),
                        observed_at=observed_now,
                        raw_checksum=rec_hash,
                        ingestion_run_id=ingestion_run_id
                    )
                    expectations.append(exp)

            pages_fetched += 1
            if len(page_items) < self.page_size or pages_fetched >= self.max_pages:
                break

            skip += self.page_size

        return expectations
