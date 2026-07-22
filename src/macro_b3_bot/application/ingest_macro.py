from __future__ import annotations

import uuid
import yaml
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.bcb.sgs_client import BcbSgsClient
from macro_b3_bot.adapters.bcb.expectations_client import BcbExpectationsClient
from macro_b3_bot.infrastructure.store import DatabaseStore

class MacroIngestionPipeline:
    """
    Orquestrador de ingestão de dados macroeconômicos do BCB (SGS e Focus).
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
        total_received = 0
        total_valid = 0
        total_rejected = 0

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
                total_received += len(obs_list)
                for obs in obs_list:
                    store.save_macro_observation(obs.model_dump(mode="json"))
                    total_valid += 1
            except Exception as e:
                total_rejected += 1

        store.finish_ingestion_run(run_id, "SUCCESS", total_received, total_valid, total_rejected)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "series_count": len(series_list),
            "received": total_received,
            "valid": total_valid,
            "rejected": total_rejected
        }

    async def ingest_bcb_focus(self, since: date) -> Dict[str, Any]:
        run_id = f"RUN_FOCUS_{uuid.uuid4().hex[:8]}"
        store = DatabaseStore(self.db_path)
        store.start_ingestion_run(run_id, "BCB_FOCUS")

        indicators = ["IPCA", "Selic", "Câmbio", "PIB Total"]
        total_received = 0
        total_valid = 0
        total_rejected = 0

        for ind in indicators:
            try:
                exp_list = await self.expectations_client.fetch_annual_expectations(
                    indicator=ind,
                    since=since,
                    ingestion_run_id=run_id
                )
                total_received += len(exp_list)
                for exp in exp_list:
                    store.save_market_expectation(exp.model_dump(mode="json"))
                    total_valid += 1
            except Exception as e:
                total_rejected += 1

        store.finish_ingestion_run(run_id, "SUCCESS", total_received, total_valid, total_rejected)
        store.close()

        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "indicators_count": len(indicators),
            "received": total_received,
            "valid": total_valid,
            "rejected": total_rejected
        }
