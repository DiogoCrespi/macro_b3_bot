"""
Ingestion of global macro series (FRED, EIA, NOAA) — Sprint 4A.

Orchestrates fetching from each adapter, normalises to MacroRelease dicts,
computes previous_value from the stored series history, and persists to DuckDB.

Design principles:
- Incremental: only fetches since the last stored observation for each series.
- Idempotent: record_checksum deduplicates on re-runs.
- No look-ahead: available_at is set to NOW() at ingestion time.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "global_macro_series.yaml"


def _load_series_config(config_path: Path = _CONFIG_PATH) -> list[dict]:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["series"]


def _get_last_reference_date(store: DatabaseStore, source: str, series_code: str) -> Optional[date]:
    """Return the most recent reference_date stored for this series, or None."""
    row = store.connection.execute(
        """
        SELECT MAX(reference_date) FROM macro_releases
        WHERE source = ? AND series_code = ?
        """,
        [source, series_code]
    ).fetchone()
    if row and row[0]:
        d = row[0]
        return d if isinstance(d, date) else date.fromisoformat(str(d))
    return None


def _get_previous_value(store: DatabaseStore, source: str, series_code: str, ref_date: date) -> Optional[Decimal]:
    """Return the actual_value of the most recent stored release BEFORE ref_date."""
    row = store.connection.execute(
        """
        SELECT actual_value FROM macro_releases
        WHERE source = ? AND series_code = ? AND reference_date < ?
        ORDER BY reference_date DESC LIMIT 1
        """,
        [source, series_code, ref_date]
    ).fetchone()
    return Decimal(str(row[0])) if row and row[0] is not None else None


class GlobalMacroIngester:
    """
    Coordinates ingestion from FRED, EIA, and NOAA.

    Usage:
        ingester = GlobalMacroIngester(settings, incremental=True)
        result = ingester.run(sources=['fred', 'eia', 'noaa'])
    """

    def __init__(
        self,
        settings: Settings,
        incremental: bool = True,
        config_path: Path = _CONFIG_PATH,
    ) -> None:
        self.settings = settings
        self.incremental = incremental
        self.config_path = config_path
        db_path = settings.data_dir / "audit.duckdb"
        self.store = DatabaseStore(db_path)
        self.run_id = str(uuid.uuid4())
        self.available_at = datetime.now(timezone.utc)

    def run(self, sources: Optional[list[str]] = None) -> dict:
        series_list = _load_series_config(self.config_path)
        if sources:
            src_lower = {s.lower() for s in sources}
            series_list = [s for s in series_list if s["source"].lower().split("_")[0] in src_lower]

        total_new = 0
        total_skipped = 0
        failed_series: list[str] = []

        for series_cfg in series_list:
            source = series_cfg["source"]
            series_code = series_cfg["series_code"]
            try:
                new, skipped = self._ingest_series(series_cfg)
                total_new += new
                total_skipped += skipped
                logger.info("[%s] %s: +%d new, %d skipped", source, series_code, new, skipped)
            except Exception as exc:
                logger.error("[%s] %s: ingestion failed — %s", source, series_code, exc)
                failed_series.append(f"{source}/{series_code}")

        return {
            "run_id": self.run_id,
            "series_processed": len(series_list),
            "releases_new": total_new,
            "releases_skipped": total_skipped,
            "failed_series": failed_series,
        }

    def _ingest_series(self, cfg: dict) -> tuple[int, int]:
        source = cfg["source"]
        series_code = cfg["series_code"]
        lookback_days = cfg.get("lookback_days", 730)

        # Determine start date
        if self.incremental:
            last = _get_last_reference_date(self.store, source, series_code)
            if last:
                start_date = last - timedelta(days=7)  # overlap to catch revisions
            else:
                start_date = date.today() - timedelta(days=lookback_days)
        else:
            start_date = date.today() - timedelta(days=lookback_days)

        end_date = date.today()

        if source == "FRED":
            return self._ingest_fred(cfg, start_date, end_date)
        elif source == "EIA":
            return self._ingest_eia(cfg, start_date, end_date)
        elif source == "NOAA":
            return self._ingest_noaa(cfg)
        elif source in ("BCB_SGS", "BCB_FOCUS"):
            logger.debug("BCB series %s/%s handled by existing BCB ingestor", source, series_code)
            return 0, 0
        else:
            logger.warning("Unknown source '%s', skipping %s", source, series_code)
            return 0, 0

    def _ingest_fred(self, cfg: dict, start_date: date, end_date: date) -> tuple[int, int]:
        from macro_b3_bot.adapters.macro.fred_client import FredClient, normalize_fred_observation

        api_key = self.settings.fred_api_key
        if not api_key:
            raise ValueError("FRED_API_KEY not configured — set FRED_API_KEY in .env")

        client = FredClient(api_key)
        observations = client.fetch_series_observations(
            series_id=cfg["series_code"],
            observation_start=start_date,
            observation_end=end_date,
            realtime_start=start_date,   # ALFRED: use realtime to get vintage info
        )

        new = 0
        skipped = 0
        for obs in observations:
            payload = normalize_fred_observation(
                obs=obs,
                series_code=cfg["series_code"],
                indicator=cfg["indicator"],
                geography=cfg["geography"],
                frequency=cfg["frequency"],
                unit=cfg["unit"],
                ingestion_run_id=self.run_id,
                available_at=self.available_at,
            )
            if payload is None:
                skipped += 1
                continue

            # Attach previous_value from stored history
            payload["previous_value"] = _get_previous_value(
                self.store, "FRED", cfg["series_code"], payload["reference_date"]
            )

            saved = self.store.save_macro_release(payload)
            if saved:
                new += 1
            else:
                skipped += 1

        return new, skipped

    def _ingest_eia(self, cfg: dict, start_date: date, end_date: date) -> tuple[int, int]:
        from macro_b3_bot.adapters.macro.eia_client import EiaClient, normalize_eia_observation

        api_key = self.settings.eia_api_key
        if not api_key:
            raise ValueError("EIA_API_KEY not configured — set EIA_API_KEY in .env")

        client = EiaClient(api_key)
        observations = client.fetch_series_observations(
            series_id=cfg["series_code"],
            start_date=start_date,
            end_date=end_date,
        )

        new = 0
        skipped = 0
        for obs in observations:
            payload = normalize_eia_observation(
                obs=obs,
                series_code=cfg["series_code"],
                indicator=cfg["indicator"],
                geography=cfg["geography"],
                frequency=cfg["frequency"],
                unit=cfg["unit"],
                ingestion_run_id=self.run_id,
                available_at=self.available_at,
            )
            if payload is None:
                skipped += 1
                continue

            payload["previous_value"] = _get_previous_value(
                self.store, "EIA", cfg["series_code"], payload["reference_date"]
            )

            saved = self.store.save_macro_release(payload)
            if saved:
                new += 1
            else:
                skipped += 1

        return new, skipped

    def _ingest_noaa(self, cfg: dict) -> tuple[int, int]:
        from macro_b3_bot.adapters.macro.noaa_enso_client import NoaaEnsoClient, normalize_noaa_observation

        client = NoaaEnsoClient()
        records = client.fetch_nino34_and_oni()

        new = 0
        skipped = 0
        for record in records:
            payload = normalize_noaa_observation(
                record=record,
                series_code=cfg["series_code"],
                indicator=cfg["indicator"],
                geography=cfg["geography"],
                frequency=cfg["frequency"],
                unit=cfg["unit"],
                ingestion_run_id=self.run_id,
                available_at=self.available_at,
            )
            if payload is None:
                skipped += 1
                continue

            payload["previous_value"] = _get_previous_value(
                self.store, "NOAA", cfg["series_code"], payload["reference_date"]
            )

            saved = self.store.save_macro_release(payload)
            if saved:
                new += 1
            else:
                skipped += 1

        return new, skipped

    def close(self) -> None:
        self.store.close()
