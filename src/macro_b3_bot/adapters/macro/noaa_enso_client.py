"""
NOAA ENSO adapter — Sprint 4A.

Fetches Niño 3.4 SST anomaly and Oceanic Niño Index (ONI) from NOAA/NCEI.
Data source: https://www.ncei.noaa.gov/access/monitoring/enso/

NOAA does not require an API key for bulk CSV/JSON downloads.
This adapter fetches the monthly tables directly from the official NOAA endpoints.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from macro_b3_bot.adapters.macro.fred_client import (
    make_raw_checksum,
    make_record_checksum,
    make_release_id,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0

# NOAA CPC (Climate Prediction Center) ONI table
NOAA_ONI_URL = "https://origin.cpc.ncep.noaa.gov/products/analysis_monitoring/ensostuff/detrend.nino34.ascii.txt"

# NOAA NCEI Niño 3.4 monthly data
NOAA_NINO34_URL = "https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr-only/"

# CPC SST anomaly data (best source for Niño 3.4 + ONI)
NOAA_CPC_SST_URL = "https://origin.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii"


# ENSO classification thresholds (ONI-based)
ENSO_EL_NINO_THRESHOLD = Decimal("0.5")
ENSO_LA_NINA_THRESHOLD = Decimal("-0.5")
ENSO_STRONG_THRESHOLD = Decimal("1.5")


class NoaaEnsoClient:
    """
    Client for NOAA ENSO data (Niño 3.4 and ONI).

    Uses the CPC monthly SST anomaly table which contains both series.
    No API key required — public data from NOAA CPC.
    """

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def fetch_nino34_and_oni(self) -> list[dict]:
        """
        Fetch the complete Niño 3.4 and ONI monthly record from NOAA CPC.

        Returns list of dicts with keys:
            year, month, nino34_sst, nino34_anom, oni_3month
        """
        raw_text = self._fetch_text(NOAA_CPC_SST_URL)
        return self._parse_cpc_sst_table(raw_text)

    def _fetch_text(self, url: str) -> str:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            raise RuntimeError(f"NOAA request failed for {url}: {exc}") from exc

    def _parse_cpc_sst_table(self, text: str) -> list[dict]:
        """
        Parse NOAA CPC SST table.

        Format (space-separated):
            YR  MON  TOTAL  ClimAdjSSTAnom  Nino3  Anom  Nino34  Anom  Nino4  Anom  ONI  ...

        We extract: YR, MON, Nino34 anomaly (col 8), and 3-month ONI (col 11).
        """
        records = []
        lines = text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line or line.startswith("YR") or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 11:
                continue
            try:
                year = int(parts[0])
                month = int(parts[1])
                nino34_anom = Decimal(parts[7])   # Niño 3.4 SST anomaly (col index 7)
                # ONI is a 3-month centred average — col 10 in this table
                oni = Decimal(parts[10]) if len(parts) > 10 else None
            except (ValueError, InvalidOperation, IndexError):
                continue

            records.append({
                "year": year,
                "month": month,
                "nino34_anom": nino34_anom,
                "oni": oni,
            })
        return records


def classify_enso_phase(oni_value: Optional[Decimal]) -> str:
    """Return ENSO phase label based on ONI value."""
    if oni_value is None:
        return "NEUTRAL"
    if oni_value >= ENSO_EL_NINO_THRESHOLD:
        return "EL_NINO"
    if oni_value <= ENSO_LA_NINA_THRESHOLD:
        return "LA_NINA"
    return "NEUTRAL"


def classify_enso_intensity(oni_value: Optional[Decimal]) -> str:
    """Return ENSO intensity: WEAK, MODERATE, STRONG, VERY_STRONG, NEUTRAL."""
    if oni_value is None:
        return "NEUTRAL"
    abs_oni = abs(oni_value)
    if abs_oni < ENSO_EL_NINO_THRESHOLD:
        return "NEUTRAL"
    if abs_oni < Decimal("1.0"):
        return "WEAK"
    if abs_oni < ENSO_STRONG_THRESHOLD:
        return "MODERATE"
    if abs_oni < Decimal("2.0"):
        return "STRONG"
    return "VERY_STRONG"


def normalize_noaa_observation(
    record: dict,
    series_code: str,
    indicator: str,
    geography: list[str],
    frequency: str,
    unit: str,
    ingestion_run_id: str,
    available_at: datetime,
) -> Optional[dict]:
    """
    Convert a NOAA ENSO record to a normalised MacroRelease payload dict.

    series_code determines which value is extracted:
        'NINO34'  → nino34_anom
        'ONI'     → oni
    """
    year = record.get("year")
    month = record.get("month")
    if not year or not month:
        return None

    try:
        ref_date = date(year, month, 1)
    except ValueError:
        return None

    if series_code == "NINO34":
        value = record.get("nino34_anom")
    elif series_code == "ONI":
        value = record.get("oni")
    else:
        return None

    if value is None:
        return None

    # NOAA data is published monthly; use first day of following month as published_at
    pub_month = month + 1 if month < 12 else 1
    pub_year = year if month < 12 else year + 1
    published_at = datetime(pub_year, pub_month, 1, tzinfo=timezone.utc)

    release_id = make_release_id("NOAA", series_code, ref_date, published_at)
    raw_chk = make_raw_checksum({"source": "NOAA", "series": series_code, "year": year, "month": month, "value": str(value)})
    rec_chk = make_record_checksum("NOAA", series_code, ref_date, str(value))

    # Derived metadata for ENSO
    enso_phase = classify_enso_phase(value) if series_code in ("NINO34", "ONI") else "N/A"
    enso_intensity = classify_enso_intensity(value) if series_code in ("NINO34", "ONI") else "N/A"

    return {
        "release_id": release_id,
        "source": "NOAA",
        "series_code": series_code,
        "indicator": indicator,
        "geography": geography,
        "frequency": frequency,
        "unit": unit,
        "reference_date": ref_date,
        "published_at": published_at,
        "available_at": available_at,
        "actual_value": value,
        "previous_value": None,        # populated by ingest layer
        "revised_previous_value": None,
        "consensus_value": None,
        "raw_checksum": raw_chk,
        "record_checksum": rec_chk,
        "ingestion_run_id": ingestion_run_id,
        # ENSO-specific extras (stored in score_breakdown / event metadata)
        "_enso_phase": enso_phase,
        "_enso_intensity": enso_intensity,
    }
