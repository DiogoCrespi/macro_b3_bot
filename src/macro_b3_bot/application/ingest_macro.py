from __future__ import annotations

import uuid
import yaml
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Any

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.bcb.sgs_client import BcbSgsClient
from macro_b3_bot.adapters.bcb.expectations_client import BcbExpectationsClient
from macro_b3_bot.infrastructure.store import DatabaseStore

@dataclass
class IngestionResult:
    received: int = 0
    inserted: int = 0
    duplicated: int = 0
    revised: int = 0
    rejected: int = 0

class MacroIngestionPipeline:
    """
    Orquestrador de ingestão de dados macroeconômicos do BCB (SGS e Focus) com rastreio estrito de duplicidade.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.raw_dir = settings.data_dir / "raw" / "bcb"
        self.db_path = settings.data_dir / "audit.duckdb"
        self.sgs_client = BcbSgsClient(raw_cache_dir=self.raw_dir / "sgs")
        self.expectations_client = BcbExpectationsClient(raw_cache_dir=self.raw_dir / "expectations")

    async def ingest_bcb_sgs(self, start_date: date, end_date: date) -> Dict[str, Any]:
        run_id = f"RUN_SGS_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, "BCB_SGS")

        config_yaml = Path(__file__).resolve().parent.parent.parent.parent / "config" / "bcb_series.yaml"
        if not config_yaml.exists():
            store.finish_ingestion_run(run_id, "FAILED", 0, 0, 0, f"Config {config_yaml} nao encontrado")
            store.close()
            return {"status": "FAILED", "error": "config_missing"}

        with open(config_yaml, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        series_list = cfg.get("series", [])
        result = IngestionResult()

        for s in series_list:
            code = s["code"]
            name = s["name"]
            unit = s["unit"]
            frequency = s["frequency"]

            try:
                obs_list = await self.sgs_client.fetch_series(
                    code=code,
                    name=name,
                    unit=unit,
                    frequency=frequency,
                    start_date=start_date,
                    end_date=end_date,
                    ingestion_run_id=run_id
                )
                result.received += len(obs_list)
                for obs in obs_list:
                    was_inserted = store.save_macro_observation(obs.model_dump(mode="json"))
                    if was_inserted:
                        result.inserted += 1
                    else:
                        result.duplicated += 1
            except Exception as e:
                result.rejected += 1
                print(f"    ❌ [ERROR SGS {code}]: {e}")

        store.finish_ingestion_run(run_id, "SUCCESS", result.received, result.inserted, result.rejected)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "series_count": len(series_list),
            "received": result.received,
            "inserted": result.inserted,
            "duplicated": result.duplicated,
            "rejected": result.rejected
        }

    async def ingest_bcb_focus(self, since: date) -> Dict[str, Any]:
        run_id = f"RUN_FOCUS_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, "BCB_FOCUS")

        indicators = ["IPCA", "Selic", "Câmbio", "PIB Total"]
        result = IngestionResult()

        for ind in indicators:
            try:
                exp_list = await self.expectations_client.fetch_annual_expectations(
                    indicator=ind,
                    since=since,
                    ingestion_run_id=run_id
                )
                result.received += len(exp_list)
                for exp in exp_list:
                    was_inserted = store.save_market_expectation(exp.model_dump(mode="json"))
                    if was_inserted:
                        result.inserted += 1
                    else:
                        result.duplicated += 1
            except Exception as e:
                result.rejected += 1
                print(f"    ❌ [ERROR FOCUS {ind}]: {e}")

        store.finish_ingestion_run(run_id, "SUCCESS", result.received, result.inserted, result.rejected)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "indicators_count": len(indicators),
            "received": result.received,
            "inserted": result.inserted,
            "duplicated": result.duplicated,
            "rejected": result.rejected
        }
