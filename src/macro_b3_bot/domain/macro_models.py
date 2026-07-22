from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field

class MacroObservation(BaseModel):
    source: str
    series_code: str
    indicator: str
    reference_date: date
    observed_at: datetime
    available_at: datetime | None = None
    value: Decimal
    unit: str
    frequency: str
    revision: int = 0
    raw_checksum: str
    ingestion_run_id: str

class MarketExpectation(BaseModel):
    source: str = "BCB_FOCUS"
    indicator: str
    reference_date: date
    target_period: str
    statistic: str
    value: Decimal
    base_calculation: int | None = None
    observed_at: datetime
    raw_checksum: str
    ingestion_run_id: str

class MacroSurprise(BaseModel):
    indicator: str
    reference_date: date
    current_value: Decimal
    previous_value: Decimal | None = None
    delta: Decimal | None = None
    percent_change: Decimal | None = None
    rolling_zscore: float | None = None
    expectation_value: Decimal | None = None
    expectation_error: Decimal | None = None
