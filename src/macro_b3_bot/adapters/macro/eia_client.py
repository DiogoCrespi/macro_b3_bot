"""
EIA Open Data adapter — Sprint 4A.

Fetches energy series (WTI, Brent, crude stocks, production, refinery, gasoline).
API docs: https://www.eia.gov/opendata/

Environment variable required: EIA_API_KEY
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlencode

import httpx

from macro_b3_bot.adapters.macro.fred_client import (
    make_raw_checksum,
    make_record_checksum,
    make_release_id,
)

logger = logging.getLogger(__name__)

EIA_BASE = "https://api.eia.gov/v2"
_DEFAULT_TIMEOUT = 20.0
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0

# Map EIA series_code → API route and facet value
# EIA v2 API uses: /v2/seriesid/{series_id}/data?api_key=...
# The series_code stored in config is the full EIA series ID (e.g. PET.RWTC.D)
EIA_FREQUENCY_MAP = {
    "D": "DAILY",
    "W": "WEEKLY",
    "M": "MONTHLY",
    "Q": "QUARTERLY",
    "A": "ANNUAL",
}


class EiaClient:
    """
    Client for EIA Open Data API v2.

    EIA does not provide vintage/revision tracking. Data quality score
    will be penalised accordingly (penalty_no_vintage applied in scoring).
    """

    def __init__(self, api_key: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if not api_key:
            raise ValueError("EIA_API_KEY must be set")
        self._api_key = api_key
        self._timeout = timeout

    def fetch_series_observations(
        self,
        series_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        max_rows: int = 5000,
    ) -> list[dict]:
        """
        Fetch observations for an EIA series.

        EIA v2 seriesid endpoint returns list of {period, value, ...}.
        period format varies: 'YYYY-MM-DD' (daily), 'YYYY-WXX' (weekly), 'YYYY-MM' (monthly).

        Returns list of raw dicts.
        """
        params: dict = {
            "api_key": self._api_key,
            "length": max_rows,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
        }
        if start_date:
            params["start"] = start_date.isoformat()
        if end_date:
            params["end"] = end_date.isoformat()

        url = f"{EIA_BASE}/seriesid/{series_id}/data?{urlencode(params)}"
        raw = self._get(url)
        # EIA v2 nests data under response.data
        response = raw.get("response", raw)
        return response.get("data", [])

    def _get(self, url: str) -> dict:
        last_err: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    return resp.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_err = exc
                logger.warning("EIA request failed (attempt %d/%d): %s", attempt, _RETRY_ATTEMPTS, exc)
                if attempt < _RETRY_ATTEMPTS:
                    time.sleep(_RETRY_DELAY * attempt)
        raise RuntimeError(f"EIA request failed after {_RETRY_ATTEMPTS} attempts: {last_err}") from last_err


def parse_eia_period(period_str: str) -> Optional[date]:
    """
    Parse EIA period strings to date.
    Formats observed: 'YYYY-MM-DD', 'YYYY-WXX', 'YYYY-MM', 'YYYY'
    """
    if not period_str:
        return None
    # Daily
    if len(period_str) == 10 and period_str[4] == "-" and period_str[7] == "-":
        try:
            return date.fromisoformat(period_str)
        except ValueError:
            return None
    # Monthly YYYY-MM → first day of month
    if len(period_str) == 7 and period_str[4] == "-":
        try:
            return date.fromisoformat(period_str + "-01")
        except ValueError:
            return None
    # Weekly YYYY-WXX → approximate: return Monday of that ISO week
    if "W" in period_str and len(period_str) <= 8:
        try:
            year_part, week_part = period_str.split("W")
            from datetime import timedelta
            year = int(year_part)
            week = int(week_part)
            # ISO week: Jan 4 is always in week 1
            jan4 = date(year, 1, 4)
            start_of_week1 = jan4 - timedelta(days=jan4.weekday())
            return start_of_week1 + timedelta(weeks=week - 1)
        except Exception:
            return None
    # Annual YYYY
    if len(period_str) == 4:
        try:
            return date(int(period_str), 1, 1)
        except ValueError:
            return None
    return None


def normalize_eia_observation(
    obs: dict,
    series_code: str,
    indicator: str,
    geography: list[str],
    frequency: str,
    unit: str,
    ingestion_run_id: str,
    available_at: datetime,
) -> Optional[dict]:
    """Convert a raw EIA observation to normalised MacroRelease payload dict."""
    period_str = obs.get("period", "")
    ref_date = parse_eia_period(period_str)
    if ref_date is None:
        logger.warning("EIA: cannot parse period '%s' for %s", period_str, series_code)
        return None

    raw_value = obs.get("value")
    if raw_value is None or raw_value == "":
        return None
    try:
        value = Decimal(str(raw_value))
    except InvalidOperation:
        return None

    # EIA doesn't publish exact release timestamps in v2 history API; mark precision UNKNOWN and set available_at to collected_at
    published_at = None
    collected_at = available_at
    rel_available_at = collected_at

    release_id = make_release_id("EIA", series_code, ref_date, rel_available_at)
    raw_chk = make_raw_checksum({"source": "EIA", "series": series_code, "period": period_str, "value": str(value)})
    rec_chk = make_record_checksum("EIA", series_code, ref_date, str(value), unit)

    return {
        "release_id": release_id,
        "source": "EIA",
        "series_code": series_code,
        "indicator": indicator,
        "geography": geography,
        "frequency": frequency,
        "unit": unit,
        "reference_date": ref_date,
        "published_at": published_at,
        "available_at": rel_available_at,
        "collected_at": collected_at,
        "vintage_date": ref_date,
        "realtime_start": None,
        "realtime_end": None,
        "availability_precision": "UNKNOWN",
        "revision_number": 0,
        "is_initial_release": True,
        "actual_value": value,
        "previous_value": None,
        "revised_previous_value": None,
        "consensus_value": None,
        "raw_checksum": raw_chk,
        "record_checksum": rec_chk,
        "ingestion_run_id": ingestion_run_id,
    }
