from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from macro_b3_bot.domain.models import AssetClass, AssetSnapshot


class B3ScreenerJsonBridge:
    """Reads a versioned JSON export produced by the existing Node.js screener."""

    def __init__(self, export_path: Path):
        self.export_path = export_path

    def load_assets(self) -> list[AssetSnapshot]:
        if not self.export_path.exists():
            raise FileNotFoundError(
                f"B3 screener export not found: {self.export_path}. "
                "Create a versioned export before running the pipeline."
            )
        payload = json.loads(self.export_path.read_text(encoding="utf-8"))
        records = payload.get("records", payload if isinstance(payload, list) else [])
        if not isinstance(records, list):
            raise ValueError("invalid B3 screener export: expected records[]")
        return [self._parse(record, payload) for record in records]

    def _parse(self, record: dict[str, Any], envelope: dict[str, Any]) -> AssetSnapshot:
        ticker = str(record.get("ticker") or record.get("symbol") or "").upper().strip()
        if not ticker:
            raise ValueError("record without ticker")
        kind = str(record.get("asset_class") or record.get("type") or "stock").lower()
        mapping = {
            "acao": AssetClass.STOCK,
            "stock": AssetClass.STOCK,
            "fii": AssetClass.FII,
            "etf": AssetClass.ETF,
            "bdr": AssetClass.BDR,
        }
        asset_class = mapping.get(kind, AssetClass.STOCK)
        as_of_raw = record.get("as_of") or envelope.get("generated_at")
        as_of = datetime.fromisoformat(as_of_raw.replace("Z", "+00:00")) if as_of_raw else datetime.now().astimezone()
        price = float(record.get("price") or record.get("regularMarketPrice") or 0)
        if price <= 0:
            raise ValueError(f"{ticker}: invalid price {price}")
        volume = float(record.get("avg_daily_volume_brl") or record.get("liquidity") or 0)
        reserved = {"ticker", "symbol", "asset_class", "type", "as_of", "price", "regularMarketPrice", "avg_daily_volume_brl", "liquidity", "sector"}
        metrics = {key: value for key, value in record.items() if key not in reserved and isinstance(value, (int, float, type(None)))}
        return AssetSnapshot(
            ticker=ticker,
            asset_class=asset_class,
            as_of=as_of,
            price=price,
            avg_daily_volume_brl=volume,
            sector=record.get("sector"),
            metrics=metrics,
            source_fields={key: "b3_screener_export" for key in metrics},
        )
