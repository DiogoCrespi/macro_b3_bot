"""Sprint 4F Research Timing, Risk & Invalidation Application Service.

Technical Debt Notes:
- Market pricing risk proxies utilize historical price window percentiles and volume medians.
- Thesis invalidators are structured rules derived from macro conflict indicators and PIT availability.

This module evaluates timing, event freshness, pricing risk, volatility/liquidity states,
thesis invalidators, and review triggers for a company based on its 4E.3 ResearchDecisionSnapshot.

Outputs are strictly MONITOR, WAIT_FOR_CONFIRMATION, or AVOID.
DCF valuation, target prices, BUY recommendations, MiroFish scenarios, and order execution remain STRICTLY BLOCKED.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot


class ResearchTimingRiskSynthesizer:
    """
    Synthesizes upstream decision snapshots and market dynamics into a deterministic ResearchTimingRiskSnapshot.
    """
    methodology_version = "4F.1-research-timing-risk-v1"

    def synthesize(
        self,
        *,
        decision_snapshot: ResearchDecisionSnapshot,
        as_of_timestamp: datetime | str,
        market_quotes: list[dict[str, Any]] | None = None,
        volatility_metrics: dict[str, Any] | None = None,
        liquidity_metrics: dict[str, Any] | None = None,
        input_ids: dict[str, Any] | None = None,
    ) -> ResearchTimingRiskSnapshot:
        market_quotes = market_quotes or []
        volatility_metrics = volatility_metrics or {}
        liquidity_metrics = liquidity_metrics or {}
        input_ids = input_ids or {}

        if isinstance(as_of_timestamp, datetime):
            if as_of_timestamp.tzinfo is None:
                as_of_dt = as_of_timestamp.replace(tzinfo=timezone.utc)
            else:
                as_of_dt = as_of_timestamp.astimezone(timezone.utc)
            as_of_str = as_of_dt.isoformat()
        else:
            as_of_str = str(as_of_timestamp)
            as_of_dt = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
            if as_of_dt.tzinfo is None:
                as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

        ticker = decision_snapshot.ticker
        decision = decision_snapshot.decision
        critical_blockers = decision_snapshot.critical_blockers

        catalysts = []
        for evt_id in decision_snapshot.macro_event_ids:
            catalysts.append({
                "event_id": evt_id,
                "status": "ACTIVE_MONITORED",
                "importance": "HIGH",
            })

        expected_horizon = {
            "min_horizon_days": 30,
            "target_horizon_days": 180,
            "max_horizon_days": 360,
        }

        event_freshness = {
            "freshness_status": "FRESH" if decision_snapshot.macro_event_ids else "STALE_OR_MISSING",
            "decay_factor": 1.0 if decision_snapshot.macro_event_ids else 0.0,
        }

        pricing_risk = {
            "priced_in_estimate": "UNKNOWN_DATA_INCOMPLETE" if decision == "NO_ACTION" else "PARTIALLY_PRICED",
            "market_discount_pct": None,
        }

        volatility_state = {
            "volatility_status": volatility_metrics.get("status", "NORMAL"),
            "historical_volatility": volatility_metrics.get("historical_vol", 0.25),
        }

        liquidity_state = {
            "liquidity_status": liquidity_metrics.get("status", "ADEQUATE"),
            "daily_volume_brl": liquidity_metrics.get("daily_volume", 50000000.0),
        }

        thesis_invalidators = list(decision_snapshot.invalidation_conditions)
        for b in critical_blockers:
            if b not in thesis_invalidators:
                thesis_invalidators.append(b)

        risk_flags = []
        if decision_snapshot.execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            risk_flags.append(f"UPSTREAM_EXECUTION_MODE_{decision_snapshot.execution_mode}")
        for b in critical_blockers:
            risk_flags.append(f"BLOCKER_{b}")

        for w in decision_snapshot.noncritical_warnings:
            risk_flags.append(f"WARNING_{w}")

        review_triggers = [
            "Quarterly CVM statement release",
            "Macro interest rate / FX decision update",
            "Material news release",
        ]

        next_review_dt = as_of_dt + timedelta(days=30)
        next_review_at = next_review_dt.isoformat()

        # Timing & Risk Classification Logic
        if decision == "NO_ACTION" or decision_snapshot.execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            timing_classification = "AVOID"
            if "CONFLICTING_MACRO_DIRECTION" in critical_blockers:
                risk_classification = "ELEVATED_RISK"
            elif "BLOCKED_MISSING_UPSTREAM_INPUT" in critical_blockers:
                risk_classification = "HIGH_RISK"
            else:
                risk_classification = "MODERATE_RISK"
        elif critical_blockers:
            timing_classification = "WAIT_FOR_CONFIRMATION"
            risk_classification = "ELEVATED_RISK"
        else:
            timing_classification = "MONITOR"
            risk_classification = "LOW_RISK" if decision_snapshot.confidence >= 0.70 else "MODERATE_RISK"

        confidence = round(decision_snapshot.confidence * 0.90, 4)

        payload_data = {
            "ticker": ticker,
            "as_of_timestamp": as_of_str,
            "research_decision_id": decision_snapshot.decision_id,
            "catalysts": catalysts,
            "expected_horizon": expected_horizon,
            "event_freshness": event_freshness,
            "pricing_risk": pricing_risk,
            "volatility_state": volatility_state,
            "liquidity_state": liquidity_state,
            "thesis_invalidators": sorted(thesis_invalidators),
            "risk_flags": sorted(risk_flags),
            "review_triggers": sorted(review_triggers),
            "next_review_at": next_review_at,
            "timing_classification": timing_classification,
            "risk_classification": risk_classification,
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
            catalysts=catalysts,
            expected_horizon=expected_horizon,
            event_freshness=event_freshness,
            pricing_risk=pricing_risk,
            volatility_state=volatility_state,
            liquidity_state=liquidity_state,
            thesis_invalidators=sorted(thesis_invalidators),
            risk_flags=sorted(risk_flags),
            review_triggers=sorted(review_triggers),
            next_review_at=next_review_at,
            timing_classification=timing_classification,
            risk_classification=risk_classification,
            confidence=confidence,
            methodology_version=self.methodology_version,
            input_ids=input_ids,
        )
