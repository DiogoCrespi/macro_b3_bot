"""
Sprint 3B Domain Models:
- MarketPrice: OHLCV com checksum imutável
- EffectiveMarketEvent: sessão de publicação + datas efetivas B3
- EventMarketMapping: cvm_code → primary_ticker + related_tickers
- EventMarketOutcome: CAR multi-janela + beta + bootstrap + classificação
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class MarketPrice(BaseModel):
    ticker: str
    trading_date: date

    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Decimal
    adjusted_close: Optional[Decimal] = None
    volume: Optional[Decimal] = None

    source: str
    collected_at: datetime
    record_checksum: str = ""

    @model_validator(mode="after")
    def compute_checksum(self) -> "MarketPrice":
        if not self.record_checksum:
            payload = f"{self.ticker}|{self.trading_date}|{self.close}|{self.source}"
            self.record_checksum = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return self


class EffectiveMarketEvent(BaseModel):
    event_id: str
    publication_timestamp: datetime
    publication_tz: str = "America/Sao_Paulo"

    publication_session: Literal["PRE_MARKET", "INTRADAY", "POST_MARKET", "NON_TRADING_DAY"]

    previous_trading_date: Optional[date] = None
    effective_trading_date: date
    first_full_trading_date: date


class EventMarketMapping(BaseModel):
    event_id: str
    cvm_code: str

    primary_ticker: str
    related_tickers: List[str] = Field(default_factory=list)
    market_symbol: str              # ticker para yfinance (ex: "PETR4.SA")

    asset_class: Literal["STOCK", "FII", "BDR", "ETF", "DEBENTURE", "UNKNOWN"] = "STOCK"
    mapping_confidence: float = Field(ge=0.0, le=1.0)
    mapping_source: str             # "STATIC_TABLE", "CNPJ_EXACT", "NAME_FUZZY"
    validated: bool = False


class EventMarketOutcome(BaseModel):
    event_id: str
    ticker: str

    publication_timestamp: datetime
    effective_trading_date: date
    publication_session: str

    prior_close: Optional[Decimal] = None

    # Retornos brutos
    raw_return_1d: Optional[float] = None
    raw_return_5d: Optional[float] = None
    raw_return_20d: Optional[float] = None
    raw_return_60d: Optional[float] = None

    # Retornos anormais acumulados (Market-Adjusted)
    car_1d: Optional[float] = None
    car_5d: Optional[float] = None
    car_20d: Optional[float] = None
    car_60d: Optional[float] = None

    # Janelas de antecipação
    pre_event_car_5d: Optional[float] = None
    event_window_car: Optional[float] = None   # [-1,+1]

    # Parâmetros de mercado
    beta: Optional[float] = None
    historical_volatility: Optional[float] = None
    volume_zscore: Optional[float] = None

    # Significância estatística
    bootstrap_pvalue_1d: Optional[float] = None
    bootstrap_pvalue_5d: Optional[float] = None
    bootstrap_pvalue_20d: Optional[float] = None

    # Classificação final
    outcome_label: Literal[
        "CONFIRMED",
        "WEAK_SIGNAL",
        "NOISE",
        "INSUFFICIENT_DATA"
    ] = "INSUFFICIENT_DATA"

    calculated_at: datetime
