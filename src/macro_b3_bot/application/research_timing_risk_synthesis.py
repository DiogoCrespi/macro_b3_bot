"""Sprint 4F.2 Research Timing, Risk & Invalidation Application Service.

Technical Debt Notes:
- Real market risk calculations use available historical market quotes up to the as_of cutoff.
- Event freshness decay uses an explicit exponential half-life model.

Outputs are strictly MONITOR, WAIT_FOR_CONFIRMATION, or AVOID.
DCF valuation, target prices, BUY recommendations, MiroFish scenarios, and order execution remain STRICTLY BLOCKED.
"""

import math
from datetime import datetime, timezone
from typing import Any
from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot

RISK_LEVEL_MAP = {
    "LOW_RISK": 1,
    "MODERATE_RISK": 2,
    "ELEVATED_RISK": 3,
    "HIGH_RISK": 4,
    "UNACCEPTABLE_RISK": 5,
}

RISK_NAME_MAP = {v: k for k, v in RISK_LEVEL_MAP.items()}


class ResearchTimingRiskSynthesizer:
    """
    Synthesizes upstream decision snapshots, real market data, and macro event timelines
    into a deterministic ResearchTimingRiskSnapshot.
    """
    methodology_version = "4F.2-research-timing-risk-v2"
    default_macro_half_life_days = 90.0

    def synthesize(
        self,
        *,
        decision_snapshot: ResearchDecisionSnapshot,
        as_of_timestamp: datetime | str,
        market_quotes: list[dict[str, Any]] | None = None,
        macro_events: list[dict[str, Any]] | None = None,
        input_ids: dict[str, Any] | None = None,
    ) -> ResearchTimingRiskSnapshot:
        market_quotes = market_quotes or []
        macro_events = macro_events or []
        input_ids = input_ids or {}

        if isinstance(as_of_timestamp, datetime):
            as_of_dt = as_of_timestamp.replace(tzinfo=timezone.utc) if as_of_timestamp.tzinfo is None else as_of_timestamp.astimezone(timezone.utc)
            as_of_str = as_of_dt.isoformat()
        else:
            as_of_str = str(as_of_timestamp)
            as_of_dt = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
            if as_of_dt.tzinfo is None:
                as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

        ticker = decision_snapshot.ticker
        decision = decision_snapshot.decision
        critical_blockers = decision_snapshot.critical_blockers
        execution_mode = decision_snapshot.execution_mode

        # 1. Real Market Metrics Calculation
        market_metrics = self._calculate_market_metrics(market_quotes, as_of_dt)

        # 2. Real Macro Event Freshness & Half-Life Decay
        catalysts, event_freshness = self._calculate_event_freshness(macro_events, decision_snapshot.macro_event_ids, as_of_dt)

        # 3. Descriptive Pricing Risk
        pricing_risk = self._calculate_pricing_risk(decision_snapshot, market_metrics, event_freshness)

        # 4. Volatility & Liquidity States from Market Metrics
        volatility_state, liquidity_state = self._extract_volatility_and_liquidity(market_metrics)

        # 5. Thesis Invalidators & Risk Flags
        thesis_invalidators = list(decision_snapshot.invalidation_conditions)
        for b in critical_blockers:
            if b not in thesis_invalidators:
                thesis_invalidators.append(b)

        risk_flags = []
        if execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            risk_flags.append(f"UPSTREAM_EXECUTION_MODE_{execution_mode}")
        for b in critical_blockers:
            risk_flags.append(f"BLOCKER_{b}")
        for w in decision_snapshot.noncritical_warnings:
            risk_flags.append(f"WARNING_{w}")

        if market_metrics.get("status") == "UNKNOWN_INSUFFICIENT_MARKET_DATA":
            risk_flags.append("MARKET_DATA_INSUFFICIENT")

        # 6. Monotonic Risk Severity Aggregation
        risk_level = self._aggregate_risk_monotonic(
            execution_mode=execution_mode,
            decision=decision,
            critical_blockers=critical_blockers,
            volatility_state=volatility_state,
            liquidity_state=liquidity_state,
        )
        risk_classification = RISK_NAME_MAP[risk_level]

        # 7. Correct Timing Semantics
        timing_classification = self._derive_timing_classification(
            decision=decision,
            execution_mode=execution_mode,
            critical_blockers=critical_blockers,
            risk_level=risk_level,
        )

        confidence = round(decision_snapshot.confidence * 0.90, 4)

        # Triggers and horizons
        review_triggers = [
            "Quarterly CVM statement release",
            "Macro interest rate / FX decision update",
            "Material news release",
        ]
        expected_horizon = {
            "target_horizon_days": 180,
            "evaluation_scope": "MEDIUM_TERM_FUNDAMENTAL",
        }

        payload_data = {
            "ticker": ticker,
            "as_of_timestamp": as_of_str,
            "research_decision_id": decision_snapshot.decision_id,
            "execution_mode": execution_mode,
            "catalysts": catalysts,
            "expected_horizon": expected_horizon,
            "event_freshness": event_freshness,
            "market_metrics": market_metrics,
            "pricing_risk": pricing_risk,
            "volatility_state": volatility_state,
            "liquidity_state": liquidity_state,
            "thesis_invalidators": sorted(thesis_invalidators),
            "risk_flags": sorted(risk_flags),
            "review_triggers": sorted(review_triggers),
            "timing_classification": timing_classification,
            "risk_classification": risk_classification,
            "risk_severity_level": risk_level,
            "confidence": confidence,
            "methodology_version": self.methodology_version,
            "input_ids": input_ids,
        }

        timing_risk_id = ResearchTimingRiskSnapshot.compute_timing_risk_id(payload_data)

        return ResearchTimingRiskSnapshot(
            timing_risk_id=timing_risk_id,
            ticker=ticker,
            as_of_timestamp=as_of_str,
            research_decision_id=decision_snapshot.decision_id,
            execution_mode=execution_mode,
            catalysts=catalysts,
            expected_horizon=expected_horizon,
            event_freshness=event_freshness,
            market_metrics=market_metrics,
            pricing_risk=pricing_risk,
            volatility_state=volatility_state,
            liquidity_state=liquidity_state,
            thesis_invalidators=sorted(thesis_invalidators),
            risk_flags=sorted(risk_flags),
            review_triggers=sorted(review_triggers),
            timing_classification=timing_classification,
            risk_classification=risk_classification,
            risk_severity_level=risk_level,
            confidence=confidence,
            methodology_version=self.methodology_version,
            input_ids=input_ids,
        )

    def _calculate_market_metrics(self, quotes: list[dict[str, Any]], as_of_dt: datetime) -> dict[str, Any]:
        """Calculates real market metrics up to as_of_dt without dummy defaults."""
        valid_quotes = []
        for q in quotes:
            raw_date = q.get("trade_date") or q.get("as_of_timestamp")
            if not raw_date:
                continue
            if isinstance(raw_date, str):
                dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            else:
                dt = datetime.combine(raw_date, datetime.min.time())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= as_of_dt:
                valid_quotes.append({
                    "date": dt,
                    "close": float(q.get("close_price") or q.get("price") or 0.0),
                    "volume": float(q.get("volume_brl") or q.get("volume") or 0.0),
                })

        valid_quotes.sort(key=lambda x: x["date"])

        if len(valid_quotes) < 21:
            return {
                "status": "UNKNOWN_INSUFFICIENT_MARKET_DATA",
                "observations_count": len(valid_quotes),
                "returns": {},
                "realized_volatility": {},
                "drawdowns": {},
                "volume": {},
            }

        prices = [q["close"] for q in valid_quotes]
        volumes = [q["volume"] for q in valid_quotes]

        # Returns calculation
        latest_price = prices[-1]
        ret_21d = (latest_price / prices[-21] - 1.0) if len(prices) >= 21 and prices[-21] > 0 else None
        ret_63d = (latest_price / prices[-63] - 1.0) if len(prices) >= 63 and prices[-63] > 0 else None
        ret_126d = (latest_price / prices[-126] - 1.0) if len(prices) >= 126 and prices[-126] > 0 else None

        # Realized Volatility (annualized)
        daily_returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                daily_returns.append(math.log(prices[i] / prices[i - 1]))

        vol_21d = self._calc_annualized_vol(daily_returns[-21:]) if len(daily_returns) >= 21 else None
        vol_63d = self._calc_annualized_vol(daily_returns[-63:]) if len(daily_returns) >= 63 else None

        # Drawdowns
        max_p_63d = max(prices[-63:]) if len(prices) >= 63 else max(prices)
        dd_63d = (latest_price - max_p_63d) / max_p_63d if max_p_63d > 0 else None

        # Average Volumes
        avg_vol_20d = sum(volumes[-20:]) / min(20, len(volumes)) if volumes else None
        avg_vol_60d = sum(volumes[-60:]) / min(60, len(volumes)) if volumes else None

        # Amihud Illiquidity Proxy: mean(|r_t| / Vol_t)
        amihud_samples = []
        for i in range(1, len(valid_quotes)):
            r = abs(daily_returns[i - 1]) if i - 1 < len(daily_returns) else 0.0
            v = valid_quotes[i]["volume"]
            if v > 0:
                amihud_samples.append(r / v)

        amihud_proxy = (sum(amihud_samples[-20:]) / len(amihud_samples[-20:])) if amihud_samples else None

        return {
            "status": "VALID_MARKET_DATA",
            "observations_count": len(valid_quotes),
            "returns": {
                "return_21d": ret_21d,
                "return_63d": ret_63d,
                "return_126d": ret_126d,
            },
            "realized_volatility": {
                "realized_vol_21d": vol_21d,
                "realized_vol_63d": vol_63d,
            },
            "drawdowns": {
                "drawdown_63d": dd_63d,
            },
            "volume": {
                "avg_volume_20d_brl": avg_vol_20d,
                "avg_volume_60d_brl": avg_vol_60d,
                "amihud_illiquidity_proxy": amihud_proxy,
            },
        }

    def _calc_annualized_vol(self, log_returns: list[float]) -> float | None:
        if len(log_returns) < 2:
            return None
        mean_r = sum(log_returns) / len(log_returns)
        variance = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
        return math.sqrt(variance) * math.sqrt(252)

    def _calculate_event_freshness(
        self,
        events: list[dict[str, Any]],
        macro_event_ids: list[str],
        as_of_dt: datetime,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Calculates half-life exponential decay for macro events."""
        events_by_id = {e.get("macro_event_id") or e.get("event_id"): e for e in events if e.get("macro_event_id") or e.get("event_id")}
        catalysts = []
        freshness_records = []

        for evt_id in macro_event_ids:
            evt_data = events_by_id.get(evt_id, {})
            raw_avail = evt_data.get("available_at") or evt_data.get("event_available_at")

            if not raw_avail:
                continue

            if isinstance(raw_avail, str):
                evt_dt = datetime.fromisoformat(raw_avail.replace("Z", "+00:00"))
            else:
                evt_dt = raw_avail
            if evt_dt.tzinfo is None:
                evt_dt = evt_dt.replace(tzinfo=timezone.utc)

            if evt_dt > as_of_dt:
                continue  # Reject future events relative to cutoff

            age_days = (as_of_dt - evt_dt).days
            decay_factor = math.pow(2.0, -age_days / self.default_macro_half_life_days)

            freshness_status = "FRESH" if age_days <= 30 else ("MODERATE" if age_days <= 90 else "STALE")

            catalysts.append({
                "event_id": evt_id,
                "status": evt_data.get("event_status", "ACTIVE_MONITORED"),
                "importance": evt_data.get("importance", "MEDIUM"),
                "age_days": age_days,
                "decay_factor": round(decay_factor, 4),
                "freshness_status": freshness_status,
            })

            freshness_records.append(decay_factor)

        if not freshness_records:
            return catalysts, {
                "freshness_status": "STALE_OR_MISSING",
                "average_decay_factor": 0.0,
                "monitored_events_count": 0,
            }

        avg_decay = sum(freshness_records) / len(freshness_records)
        overall_status = "FRESH" if avg_decay >= 0.75 else ("MODERATE" if avg_decay >= 0.50 else "STALE")

        return catalysts, {
            "freshness_status": overall_status,
            "average_decay_factor": round(avg_decay, 4),
            "monitored_events_count": len(catalysts),
        }

    def _calculate_pricing_risk(
        self,
        decision_snapshot: ResearchDecisionSnapshot,
        market_metrics: dict[str, Any],
        event_freshness: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculates descriptive pricing risk based on observed market movements and valuation percentile."""
        if market_metrics.get("status") == "UNKNOWN_INSUFFICIENT_MARKET_DATA":
            return {
                "pricing_risk_status": "UNKNOWN_INSUFFICIENT_DATA",
                "explanation": "Insufficient market quotes to assess pricing risk.",
            }

        ret_63d = market_metrics.get("returns", {}).get("return_63d")
        val_summary = decision_snapshot.historical_multiple_position

        if ret_63d is not None and abs(ret_63d) > 0.30:
            status = "POSSIBLY_PRICED"
            exp = f"Significant 63-day cumulative return of {ret_63d:.2%} observed."
        elif val_summary and val_summary.get("ev_ebitda_percentiles", {}).get("current_percentile", 0) > 0.80:
            status = "POSSIBLY_PRICED"
            exp = "Valuation is currently near upper historical percentile band."
        else:
            status = "NOT_OBVIOUSLY_PRICED"
            exp = "Observed price and valuation movement do not indicate extreme pre-pricing."

        return {
            "pricing_risk_status": status,
            "explanation": exp,
        }

    def _extract_volatility_and_liquidity(self, market_metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Extracts volatility and liquidity states strictly from real market metrics."""
        if market_metrics.get("status") == "UNKNOWN_INSUFFICIENT_MARKET_DATA":
            return (
                {"volatility_status": "UNKNOWN_INSUFFICIENT_MARKET_DATA", "historical_volatility": None},
                {"liquidity_status": "UNKNOWN_INSUFFICIENT_MARKET_DATA", "daily_volume_brl": None},
            )

        vol_21d = market_metrics.get("realized_volatility", {}).get("realized_vol_21d")
        avg_vol_20d = market_metrics.get("volume", {}).get("avg_volume_20d_brl")

        vol_status = "NORMAL"
        if vol_21d is not None:
            if vol_21d > 0.50:
                vol_status = "HIGH_VOLATILITY"
            elif vol_21d < 0.15:
                vol_status = "LOW_VOLATILITY"

        liq_status = "ADEQUATE"
        if avg_vol_20d is not None:
            if avg_vol_20d < 1000000.0:
                liq_status = "LOW_LIQUIDITY"
            elif avg_vol_20d > 50000000.0:
                liq_status = "HIGH_LIQUIDITY"

        return (
            {"volatility_status": vol_status, "realized_volatility": vol_21d},
            {"liquidity_status": liq_status, "daily_volume_brl": avg_vol_20d},
        )

    def _aggregate_risk_monotonic(
        self,
        *,
        execution_mode: str,
        decision: str,
        critical_blockers: list[str],
        volatility_state: dict[str, Any],
        liquidity_state: dict[str, Any],
    ) -> int:
        """
        Monotonic risk severity aggregation:
        LOW (1) < MODERATE (2) < ELEVATED (3) < HIGH (4) < UNACCEPTABLE (5).
        Adding blockers or conflicts strictly INCREASES or MAINTAINS risk severity level.
        """
        levels = [RISK_LEVEL_MAP["MODERATE_RISK"]]  # Baseline level

        if execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            levels.append(RISK_LEVEL_MAP["HIGH_RISK"])

        if "BLOCKED_MISSING_UPSTREAM_INPUT" in critical_blockers:
            levels.append(RISK_LEVEL_MAP["HIGH_RISK"])

        if "CONFLICTING_MACRO_DIRECTION" in critical_blockers:
            levels.append(RISK_LEVEL_MAP["ELEVATED_RISK"])

        if "NO_ACTIVE_SECTOR_SIGNAL" in critical_blockers:
            levels.append(RISK_LEVEL_MAP["MODERATE_RISK"])

        if "NO_APPROVED_COMPANY_EXPOSURE" in critical_blockers:
            levels.append(RISK_LEVEL_MAP["MODERATE_RISK"])

        if volatility_state.get("volatility_status") == "HIGH_VOLATILITY":
            levels.append(RISK_LEVEL_MAP["ELEVATED_RISK"])

        if liquidity_state.get("liquidity_status") == "LOW_LIQUIDITY":
            levels.append(RISK_LEVEL_MAP["HIGH_RISK"])

        # Strict Maximum Severity Rule
        return max(levels)

    def _derive_timing_classification(
        self,
        *,
        decision: str,
        execution_mode: str,
        critical_blockers: list[str],
        risk_level: int,
    ) -> str:
        """
        Correct Timing Semantics:
        - NO_ACTION by missing upstream / unconfirmed inputs -> WAIT_FOR_CONFIRMATION
        - NO_ACTION by material macro conflict / active invalidators -> AVOID
        - WATCH with pending confirmation or elevated risk -> WAIT_FOR_CONFIRMATION
        - WATCH valid with acceptable risk -> MONITOR
        """
        if execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            return "WAIT_FOR_CONFIRMATION"

        if "CONFLICTING_MACRO_DIRECTION" in critical_blockers:
            return "AVOID"

        if "BLOCKED_MISSING_UPSTREAM_INPUT" in critical_blockers:
            return "WAIT_FOR_CONFIRMATION"

        if decision == "NO_ACTION":
            return "WAIT_FOR_CONFIRMATION"

        if decision == "WATCH":
            return "MONITOR" if risk_level <= 2 else "WAIT_FOR_CONFIRMATION"

        return "WAIT_FOR_CONFIRMATION"
