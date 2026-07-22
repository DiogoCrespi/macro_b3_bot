from __future__ import annotations

from datetime import date
from typing import Any
import httpx


class BcbSgsClient:
    BASE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}/dados"

    def __init__(self, timeout_seconds: float = 30):
        self.client = httpx.Client(timeout=timeout_seconds)

    def get(self, series_id: int, start: date, end: date) -> list[dict[str, Any]]:
        response = self.client.get(
            self.BASE_URL.format(series_id=series_id),
            params={
                "formato": "json",
                "dataInicial": start.strftime("%d/%m/%Y"),
                "dataFinal": end.strftime("%d/%m/%Y"),
            },
        )
        response.raise_for_status()
        return response.json()


class FredClient:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str, timeout_seconds: float = 30):
        if not api_key:
            raise ValueError("FRED API key is required")
        self.api_key = api_key
        self.client = httpx.Client(timeout=timeout_seconds)

    def get(self, series_id: str, start: date, end: date) -> list[dict[str, Any]]:
        response = self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "file_type": "json",
                "series_id": series_id,
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
            },
        )
        response.raise_for_status()
        return response.json().get("observations", [])
