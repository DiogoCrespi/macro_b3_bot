"""Sprint 4G domain models for Paper Portfolio & End-to-End Historical Replay."""

from hashlib import sha256
import json
from typing import Any, Literal
from pydantic import BaseModel, Field


class PaperPortfolioPolicy(BaseModel):
    """
    Policy specification governing simulated portfolio allocations, caps, and costs.
    """
    policy_id: str
    initial_capital: float = 100000.0
    max_weight_per_asset: float = 0.20
    max_weight_per_sector: float = 0.40
    min_cash_weight: float = 0.20
    min_position_weight: float = 0.05
    brokerage_fee: float = 0.0
    b3_emoluments_pct: float = 0.00035
    base_slippage_pct: float = 0.0010
    low_liquidity_extra_slippage_pct: float = 0.0015
    very_low_liquidity_extra_slippage_pct: float = 0.0040
    rebalance_policy: str = "EVENT_DRIVEN"
    version: str = "4G.1-paper-policy-v1"

    @classmethod
    def compute_policy_id(cls, payload: dict[str, Any]) -> str:
        canonical = {k: v for k, v in payload.items() if k != "policy_id"}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()


class PaperAllocationEvent(BaseModel):
    """
    Audit-ready, append-only simulated portfolio allocation event.
    Strictly uses simulated terminology (SIMULATED_ENTRY, SIMULATED_EXIT, etc.).
    """
    allocation_event_id: str
    portfolio_id: str
    ticker: str
    event_type: Literal[
        "SIMULATED_ENTRY",
        "SIMULATED_EXIT",
        "SIMULATED_REBALANCE",
        "KEEP_OPEN",
        "NO_ALLOCATION",
        "INVALIDATION_EXIT",
        "HORIZON_EXIT",
    ]
    research_decision_id: str
    timing_risk_id: str
    decision_available_at: str
    execution_session: str
    execution_price: float | None = None
    price_source_id: str = "COTAHIST_OPEN"
    target_weight: float = 0.0
    executed_weight: float = 0.0
    quantity_simulated: float = 0.0
    gross_value: float = 0.0
    transaction_cost: float = 0.0
    slippage_cost: float = 0.0
    reason: str = ""
    input_ids: dict[str, Any] = Field(default_factory=dict)
    methodology_version: str = "4G.1-paper-portfolio-v1"
    created_at: str = ""

    @classmethod
    def compute_event_id(cls, payload: dict[str, Any]) -> str:
        canonical = {k: v for k, v in payload.items() if k not in ("allocation_event_id", "created_at")}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()


class PaperPositionSnapshot(BaseModel):
    """
    Snapshot of an active or historical open simulated position.
    """
    position_id: str
    portfolio_id: str
    ticker: str
    as_of_timestamp: str
    quantity: float
    average_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    weight: float
    status: Literal["OPEN", "CLOSED"] = "OPEN"


class PaperPortfolioSnapshot(BaseModel):
    """
    Daily mark-to-market snapshot of simulated portfolio NAV, cash, and position values.
    """
    portfolio_snapshot_id: str
    portfolio_id: str
    as_of_timestamp: str
    cash_balance: float
    positions_value: float
    nav: float
    daily_pnl: float
    total_realized_pnl: float
    total_transaction_costs: float
    total_slippage: float
    open_positions_count: int


class HistoricalReplayRun(BaseModel):
    """
    Run manifest for an end-to-end historical replay.
    """
    replay_run_id: str
    start_date: str
    end_date: str
    universe: list[str]
    initial_capital: float
    portfolio_policy_id: str
    methodology_versions: dict[str, str] = Field(default_factory=dict)
    source_manifest_ids: list[str] = Field(default_factory=list)
    decision_cutoffs_processed: int = 0
    market_sessions_processed: int = 0
    status: str = "COMPLETED"
    blocked_reasons: list[str] = Field(default_factory=list)
    created_at: str = ""


class HistoricalReplayStep(BaseModel):
    """
    Individual cutoff step within a historical replay run.
    """
    step_id: str
    replay_run_id: str
    cutoff_timestamp: str
    decision_ids: dict[str, str] = Field(default_factory=dict)
    timing_risk_ids: dict[str, str] = Field(default_factory=dict)
    allocation_event_ids: list[str] = Field(default_factory=list)
    nav: float
    cash_balance: float
    open_positions_count: int


class PaperPortfolioPerformanceReport(BaseModel):
    """
    Comprehensive performance report evaluating portfolio metrics, benchmarks, thesis hit rates, and blocker impacts.
    """
    report_id: str
    replay_run_id: str
    total_return_pct: float
    annualized_return_pct: float
    annualized_volatility: float
    max_drawdown_pct: float
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    turnover_ratio: float
    total_costs_brl: float
    total_slippage_brl: float
    avg_cash_weight: float
    avg_exposure_weight: float
    benchmark_returns: dict[str, float] = Field(default_factory=dict)
    thesis_metrics: dict[str, Any] = Field(default_factory=dict)
    confidence_tier_metrics: dict[str, Any] = Field(default_factory=dict)
    blocker_metrics: dict[str, Any] = Field(default_factory=dict)
    methodology_version: str = "4G.1-paper-performance-v1"
