"""
Domain models for the Global Macro Event Engine (Sprint 4A).

Pipeline: Fonte → MacroRelease → MacroEventCandidate → MacroEventGate → status
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional


@dataclass
class MacroRelease:
    """
    Single versioned observation from an official macro data source.

    Fields
    ------
    release_id          : deterministic hash of (source, series_code, reference_date, published_at)
    source              : 'FRED' | 'EIA' | 'NOAA' | 'BCB_SGS' | 'BCB_FOCUS'
    series_code         : source-native identifier (e.g. 'DFF', 'CRUDEOIL', 'NINO34')
    indicator           : human-readable name (e.g. 'Fed Funds Effective Rate')
    geography           : e.g. ['US'], ['BR'], ['GLOBAL']
    frequency           : 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'QUARTERLY'
    unit                : '%', 'USD/BBL', 'INDEX', 'MBBL', etc.

    reference_date      : period the observation refers to (NOT release date)
    published_at        : when the source officially published this value
    available_at        : when this bot first ingested it (no look-ahead after this)

    actual_value        : observed value for this release
    previous_value      : value for the previous reference period (same vintage)
    revised_previous_value : if source revised the prior period in this release
    consensus_value     : market consensus/forecast at release time (if available)

    raw_checksum        : SHA-256 of the raw API response
    record_checksum     : SHA-256 of the canonical fields used for deduplication
    ingestion_run_id    : UUID of the ingestion pipeline run
    """
    release_id: str
    source: str
    series_code: str
    indicator: str
    geography: list[str]
    frequency: str
    unit: str

    reference_date: date
    published_at: Optional[datetime]
    available_at: datetime
    collected_at: Optional[datetime] = None
    vintage_date: Optional[date] = None
    realtime_start: Optional[date] = None
    realtime_end: Optional[date] = None
    availability_precision: str = "EXACT"  # 'EXACT' | 'ESTIMATED_MONTHLY' | 'UNKNOWN'
    revision_number: int = 0
    is_initial_release: bool = True

    actual_value: Decimal = Decimal(0)
    previous_value: Optional[Decimal] = None
    revised_previous_value: Optional[Decimal] = None
    consensus_value: Optional[Decimal] = None

    raw_checksum: str = ""
    record_checksum: str = ""
    ingestion_run_id: str = ""


@dataclass
class MacroDataVintage:
    """
    Tracks historical revisions to a series observation.
    Used to reconstruct point-in-time data for backtest integrity.
    """
    vintage_id: str
    series_code: str
    source: str
    reference_date: date
    vintage_date: date          # when this particular value was published
    value: Decimal
    is_latest: bool
    ingestion_run_id: str
    created_at: datetime


@dataclass
class MacroEventCandidate:
    """
    A potential macro event detected from one or more MacroReleases.

    Scores
    ------
    surprise_score      : how much actual deviated from expectation (0–1)
    novelty_score       : how rare/unexpected this type of event is (0–1)
    persistence_score   : expected duration of the regime shift (0–1)
    regime_shift_score  : probability this represents a true regime change (0–1)
    data_quality_score  : confidence in the underlying data (0–1)

    Direction
    ---------
    'HAWKISH' | 'DOVISH' | 'RISK_ON' | 'RISK_OFF' | 'NEUTRAL'
    or indicator-specific: 'BULLISH_OIL' | 'BEARISH_OIL' | etc.

    Status
    ------
    Set by MacroEventGate:
    'MACRO_EVENT_APPROVED' | 'MACRO_EVENT_WATCH' | 'MACRO_EVENT_REJECTED'
    """
    event_id: str
    event_type: str             # from MACRO_EVENT_TYPES
    indicator: str
    geography: list[str]
    affected_variables: list[str]

    reference_date: date
    detected_at: datetime
    horizon_months: int         # expected market-relevant horizon

    actual_value: Optional[Decimal]
    expected_value: Optional[Decimal]
    surprise_value: Optional[Decimal]

    surprise_score: float
    novelty_score: float
    persistence_score: float
    regime_shift_score: float
    data_quality_score: float

    direction: str
    current_regime: str

    evidence_ids: list[str]     # release_ids that triggered this candidate
    status: str = "PENDING"

    score_breakdown: dict = field(default_factory=dict)


@dataclass
class MacroRegimeSnapshot:
    """
    Point-in-time snapshot of the macro regime matrix.
    """
    snapshot_id: str
    snapshot_date: date
    captured_at: datetime

    growth_direction: str       # 'UP' | 'DOWN' | 'STABLE'
    inflation_direction: str    # 'UP' | 'DOWN' | 'STABLE'
    liquidity_stance: str       # 'EASING' | 'TIGHTENING' | 'NEUTRAL'
    oil_regime: str             # 'SHOCK_UP' | 'SHOCK_DOWN' | 'STABLE'
    enso_phase: str             # 'EL_NINO' | 'LA_NINA' | 'NEUTRAL'

    regime_label: str           # composite e.g. 'GROWTH_UP_INFLATION_DOWN'
    confidence: float

    evidence_release_ids: list[str]
    ingestion_run_id: str


# ──────────────────────────────────────────────
# Allowed event types (Sprint 4A initial set)
# ──────────────────────────────────────────────
MACRO_EVENT_TYPES: set[str] = {
    "MONETARY_POLICY_SURPRISE",
    "INFLATION_SURPRISE",
    "GROWTH_SURPRISE",
    "YIELD_CURVE_REGIME_SHIFT",
    "USD_REGIME_SHIFT",
    "OIL_PRICE_SHOCK",
    "OIL_INVENTORY_SHOCK",
    "ENERGY_SUPPLY_SHOCK",
    "ENSO_PHASE_CHANGE",
    "ENSO_INTENSIFICATION",
    "BRAZIL_EXPECTATION_REPRICING",
}

# Allowed gate outputs
MACRO_GATE_STATUSES: set[str] = {
    "MACRO_EVENT_APPROVED",
    "MACRO_EVENT_WATCH",
    "MACRO_EVENT_REJECTED",
}

# Regime labels
MACRO_REGIMES: set[str] = {
    "GROWTH_UP_INFLATION_DOWN",
    "GROWTH_UP_INFLATION_UP",
    "GROWTH_DOWN_INFLATION_DOWN",
    "GROWTH_DOWN_INFLATION_UP",
    "LIQUIDITY_EASING",
    "LIQUIDITY_TIGHTENING",
    "OIL_SHOCK_UP",
    "OIL_SHOCK_DOWN",
    "ENSO_EL_NINO",
    "ENSO_LA_NINA",
    "MIXED",
}
