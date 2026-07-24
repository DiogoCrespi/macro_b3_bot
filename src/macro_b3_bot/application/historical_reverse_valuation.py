"""Descriptive PIT historical multiples and reverse-valuation diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import floor
from typing import Any


@dataclass(frozen=True)
class HistoricalObservation:
    ticker: str
    valuation_date: str
    market_cap: float
    enterprise_value: float
    net_income: float | None
    ebitda: float | None
    fcf_proxy: float | None
    evidence_ids: tuple[str, ...] = ()


class HistoricalMultiplesAnalyzer:
    """Never emits fair value or trading decisions."""

    @staticmethod
    def observation_id(
        ticker: str, valuation_date: str, market_snapshot_id: str,
        financial_baseline_id: str, methodology_version: str,
    ) -> str:
        payload = {
            "ticker": ticker, "valuation_date": valuation_date,
            "market_snapshot_id": market_snapshot_id,
            "financial_baseline_id": financial_baseline_id,
            "methodology_version": methodology_version,
        }
        return "hist-" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]

    @staticmethod
    def _multiple(numerator: float, denominator: float | None) -> dict[str, Any]:
        if denominator is None or denominator <= 0:
            return {"value": None, "classification": "NOT_MEANINGFUL_NONPOSITIVE_DENOMINATOR"}
        return {"value": numerator / denominator, "classification": "DESCRIPTIVE_ONLY"}

    def observe(self, item: HistoricalObservation) -> dict[str, Any]:
        return {
            "ticker": item.ticker,
            "valuation_date": item.valuation_date,
            "market_cap": item.market_cap,
            "enterprise_value": item.enterprise_value,
            "pe": self._multiple(item.market_cap, item.net_income),
            "ev_ebitda": self._multiple(item.enterprise_value, item.ebitda),
            "p_fcf_proxy": self._multiple(item.market_cap, item.fcf_proxy),
            "evidence_ids": list(item.evidence_ids),
            "classification": "DESCRIPTIVE_ONLY",
            "not_a_fair_value": True,
            "not_buy_eligible": True,
        }

    @staticmethod
    def percentiles(observations: list[dict[str, Any]], metric: str) -> dict[str, Any]:
        observations = sorted(observations, key=lambda row: row["valuation_date"])
        values = sorted(
            row[metric]["value"] for row in observations
            if row[metric]["value"] is not None
        )
        if not values:
            return {key: None for key in ("min", "p10", "p25", "median", "p75", "p90", "max", "current_percentile")}
        def quantile(q: float) -> float:
            pos = (len(values) - 1) * q
            lo, hi = floor(pos), min(floor(pos) + 1, len(values) - 1)
            return values[lo] + (values[hi] - values[lo]) * (pos - lo)
        current = observations[-1][metric]["value"]
        return {
            "min": values[0], "p10": quantile(.10), "p25": quantile(.25),
            "median": quantile(.50), "p75": quantile(.75), "p90": quantile(.90),
            "max": values[-1],
            "current_percentile": None if current is None else sum(v <= current for v in values) / len(values),
            "sample_status": "SMALL_SAMPLE_DESCRIPTIVE_ONLY" if len(values) < 8 else "DESCRIPTIVE_ONLY",
        }

    @staticmethod
    def reverse(
        observation: dict[str, Any], reference_multiple: float, metric: str,
        *, current_fundamental: float | None = None, revenue: float | None = None,
    ) -> dict[str, Any]:
        numerator_field = {
            "pe": "market_cap",
            "ev_ebitda": "enterprise_value",
            "p_fcf_proxy": "market_cap",
        }.get(metric)
        if numerator_field is None:
            raise ValueError(f"unsupported reverse metric: {metric}")
        numerator = observation[numerator_field]
        implied = None if reference_multiple <= 0 else numerator / reference_multiple
        growth = (
            None if implied is None or current_fundamental in (None, 0)
            else implied / current_fundamental - 1
        )
        margin = None if implied is None or revenue in (None, 0) else implied / revenue
        return {
            "metric": metric,
            "reference_multiple": reference_multiple,
            "implied_fundamental": implied,
            "implied_net_income": implied if metric == "pe" else None,
            "implied_ebitda": implied if metric == "ev_ebitda" else None,
            "implied_fcf": implied if metric == "p_fcf_proxy" else None,
            "implied_growth_vs_current": growth,
            "implied_margin_vs_revenue": margin,
            "classification": "PRICE_IMPLIED_FUNDAMENTALS",
            "not_a_fair_value": True,
            "not_buy_eligible": True,
        }
