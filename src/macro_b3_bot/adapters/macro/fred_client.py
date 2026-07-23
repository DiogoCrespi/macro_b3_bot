"""
FRED/ALFRED adapter — Sprint 4A.

Fetches macro series observations and vintage data from the St. Louis Fed API.
ALFRED (Archival FRED) endpoint is used for vintages to avoid look-ahead bias.

API docs: https://fred.stlouisfed.org/docs/api/fred/
Environment variable required: FRED_API_KEY
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred"
ALFRED_BASE = "https://api.stlouisfed.org/fred"
_DEFAULT_TIMEOUT = 20.0
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 2.0


class FredClient:
    """
    Thin, deterministic client for FRED/ALFRED.

    All requests are logged. No caching inside this class — caching is the
    responsibility of the DatabaseStore layer (via record_checksum).
    """

    def __init__(self, api_key: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if not api_key:
            raise ValueError("FRED_API_KEY must be set")
        self._api_key = api_key
        self._timeout = timeout

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def fetch_series_observations(
        self,
        series_id: str,
        observation_start: Optional[date] = None,
        observation_end: Optional[date] = None,
        realtime_start: Optional[date] = None,
        realtime_end: Optional[date] = None,
        output_type: Optional[int] = None,
        vintage_dates: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Fetch series observations from FRED / ALFRED.

        If realtime_start / realtime_end or vintage_dates are provided,
        this becomes an ALFRED vintage query.
        output_type:
            1 = Default (observation values)
            2 = Observations by vintage date (all)
            3 = Observations by vintage date (revised/new only)
            4 = Initial release observations only
        """
        params: dict = {
            "series_id": series_id,
            "file_type": "json",
            "api_key": self._api_key,
        }
        if observation_start:
            params["observation_start"] = observation_start.isoformat()
        if observation_end:
            params["observation_end"] = observation_end.isoformat()
        if realtime_start:
            params["realtime_start"] = realtime_start.isoformat()
        if realtime_end:
            params["realtime_end"] = realtime_end.isoformat()
        if output_type is not None:
            params["output_type"] = str(output_type)
        if vintage_dates:
            params["vintage_dates"] = ",".join(vintage_dates)

        url = f"{FRED_BASE}/series/observations?{urlencode(params)}"
        raw = self._get(url)
        return raw.get("observations", [])

    def fetch_vintage_dates(self, series_id: str) -> list[str]:
        """
        Return all vintage dates for a series (ALFRED).
        Each date represents a moment when FRED published a new data revision.
        """
        params = {
            "series_id": series_id,
            "file_type": "json",
            "api_key": self._api_key,
        }
        url = f"{ALFRED_BASE}/series/vintagedates?{urlencode(params)}"
        raw = self._get(url)
        return raw.get("vintage_dates", [])

    def fetch_series_info(self, series_id: str) -> dict:
        """Return series metadata (title, frequency, units, etc.)."""
        params = {
            "series_id": series_id,
            "file_type": "json",
            "api_key": self._api_key,
        }
        url = f"{FRED_BASE}/series?{urlencode(params)}"
        raw = self._get(url)
        slist = raw.get("seriess", [])
        return slist[0] if slist else {}

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

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
                logger.warning("FRED request failed (attempt %d/%d): %s", attempt, _RETRY_ATTEMPTS, exc)
                if attempt < _RETRY_ATTEMPTS:
                    time.sleep(_RETRY_DELAY * attempt)
        raise RuntimeError(f"FRED request failed after {_RETRY_ATTEMPTS} attempts: {last_err}") from last_err


# ──────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_fred_value(raw_value: str) -> Optional[Decimal]:
    """FRED returns '.' for missing values."""
    if raw_value in (".", "", None):
        return None
    try:
        return Decimal(raw_value)
    except InvalidOperation:
        return None


def make_release_id(source: str, series_code: str, reference_date: date, published_at: datetime) -> str:
    """Deterministic release identifier."""
    key = f"{source}|{series_code}|{reference_date.isoformat()}|{published_at.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def make_record_checksum(*fields) -> str:
    """Canonical checksum of the business-key fields for deduplication."""
    payload = "|".join(str(f) for f in fields)
    return hashlib.sha256(payload.encode()).hexdigest()


def make_raw_checksum(raw_json: dict | list) -> str:
    payload = json.dumps(raw_json, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize_fred_observation(
    obs: dict,
    series_code: str,
    indicator: str,
    geography: list[str],
    frequency: str,
    unit: str,
    ingestion_run_id: str,
    available_at: datetime,
) -> Optional[dict]:
    """
    Convert a raw FRED observation dict into a normalised MacroRelease payload dict.
    Returns None if the value is missing/invalid.
    """
    value = parse_fred_value(obs.get("value", "."))
    if value is None:
        return None

    ref_date_str = obs.get("date", "")
    try:
        ref_date = date.fromisoformat(ref_date_str)
    except ValueError:
        logger.warning("FRED: invalid date '%s' for %s", ref_date_str, series_code)
        return None

    # Use realtime_start as the publication date (earliest vintage where value exists)
    rt_start = obs.get("realtime_start", ref_date_str)
    rt_end = obs.get("realtime_end", "9999-12-31")
    try:
        published_at = datetime.fromisoformat(rt_start).replace(tzinfo=timezone.utc)
        vint_date = date.fromisoformat(rt_start)
    except ValueError:
        published_at = datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=timezone.utc)
        vint_date = ref_date

    # Historical releases became available when published; collected_at is bot run time
    rel_available_at = published_at
    collected_at = available_at

    release_id = make_release_id("FRED", series_code, ref_date, published_at)

    raw_payload = {
        "source": "FRED",
        "series_code": series_code,
        "reference_date": ref_date_str,
        "value": str(value),
        "realtime_start": rt_start,
        "realtime_end": rt_end,
    }
    raw_chk = make_raw_checksum(raw_payload)
    rec_chk = make_record_checksum("FRED", series_code, ref_date, vint_date, rt_start, rt_end, str(value), unit)

    return {
        "release_id": release_id,
        "source": "FRED",
        "series_code": series_code,
        "indicator": indicator,
        "geography": geography,
        "frequency": frequency,
        "unit": unit,
        "reference_date": ref_date,
        "published_at": published_at,
        "available_at": rel_available_at,
        "collected_at": collected_at,
        "vintage_date": vint_date,
        "realtime_start": date.fromisoformat(rt_start) if rt_start and rt_start != "9999-12-31" else None,
        "realtime_end": date.fromisoformat(rt_end) if rt_end and rt_end != "9999-12-31" else None,
        "availability_precision": "EXACT",
        "revision_number": 0,
        "is_initial_release": True,
        "actual_value": value,
        "previous_value": None,        # populated by caller in context
        "revised_previous_value": None,
        "consensus_value": None,
        "raw_checksum": raw_chk,
        "record_checksum": rec_chk,
        "ingestion_run_id": ingestion_run_id,
    }
