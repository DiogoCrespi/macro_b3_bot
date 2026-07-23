"""Factor-specific company impact without valuation, BUY, or order execution."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime

from macro_b3_bot.application.transport_company_channels import CompanyChannelTransport
from macro_b3_bot.domain.causal_models import SectorStateSnapshot
from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureSnapshot,
    CompanyFactorChannel,
    CompanyImpactCandidate,
)


_FACTOR_EXPOSURE_MATRIX: dict[tuple[str, str], tuple[str, ...]] = {
    ("FX", "revenue"): ("revenue_foreign_currency_pct", "export_revenue_pct"),
    ("FX", "cost"): ("cost_foreign_currency_pct",),
    ("FX", "debt"): ("foreign_currency_debt_pct",),
    ("INTEREST_RATES", "debt"): ("floating_rate_debt_pct",),
    ("INFLATION", "debt"): ("inflation_linked_debt_pct",),
    ("ECONOMIC_ACTIVITY", "demand"): ("demand_cyclicality",),
}


class CompanyImpactEngine:
    """Apply each macro factor only to its explicitly related company exposure."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def evaluate(
        self,
        sector: SectorStateSnapshot,
        exposure: CompanyExposureSnapshot,
        factor_impacts: dict[tuple[str, str], float] | None,
        as_of_timestamp: datetime,
        factor_channels: list[CompanyFactorChannel] | None = None,
    ) -> CompanyImpactCandidate:
        if sector.sector != exposure.sector:
            raise ValueError("sector snapshot and company exposure do not match")
        impacts = factor_impacts or CompanyChannelTransport.aggregate(
            factor_channels or []
        )
        scores: dict[str, list[float]] = {
            "revenue": [], "cost": [], "debt": [], "demand": [],
        }
        used_confidences: list[float] = []
        for (factor, channel), factor_impact in impacts.items():
            if channel not in scores:
                continue
            effect = self._factor_effect(
                factor, channel, factor_impact, exposure, used_confidences
            )
            if effect is not None:
                scores[channel].append(effect)

        components = {
            channel: self._combine(values) for channel, values in scores.items()
        }
        components["revenue"] = self._apply_modifier(
            components["revenue"], "pricing_power", exposure
        )
        components["cost"] = self._apply_modifier(
            components["cost"], "operating_leverage", exposure
        )

        missing = [name for name, value in components.items() if value is None]
        known = [value for value in components.values() if value is not None]
        net = math.tanh(sum(known)) if known else None
        field_quality = (
            sum(used_confidences) / len(used_confidences)
            if used_confidences else 0.0
        )
        confidence = (
            field_quality * sector.confidence * (len(known) / 4)
        )
        status = "WATCH" if len(known) >= 3 and confidence >= 0.5 else "NO_ACTION"
        identity = (
            f"{self.run_id}|{sector.snapshot_id}|{exposure.exposure_id}|"
            f"{as_of_timestamp.isoformat()}"
        )
        return CompanyImpactCandidate(
            candidate_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=exposure.ticker, sector_snapshot_id=sector.snapshot_id,
            company_exposure_id=exposure.exposure_id,
            as_of_timestamp=as_of_timestamp,
            revenue_impact_score=components["revenue"],
            cost_impact_score=components["cost"],
            debt_impact_score=components["debt"],
            demand_impact_score=components["demand"],
            net_company_impact=round(net, 4) if net is not None else None,
            confidence=round(confidence, 4),
            conflict_ratio=sector.conflict_ratio,
            supporting_paths=sector.supporting_event_ids,
            opposing_paths=sector.opposing_event_ids,
            missing_exposures=missing, status=status, run_id=self.run_id,
        )

    def _factor_effect(
        self,
        factor: str,
        channel: str,
        factor_impact: float,
        exposure: CompanyExposureSnapshot,
        used_confidences: list[float],
    ) -> float | None:
        if not -1 <= factor_impact <= 1:
            raise ValueError("factor impacts must be between -1 and 1")
        if factor in {"OIL", "COMMODITY"} and channel in {"revenue", "cost"}:
            sensitivity = (exposure.commodity_exposures or {}).get("OIL")
            if sensitivity is None:
                return None
            # Positive sensitivity is a producer/revenue exposure; negative
            # sensitivity is a consumer/cost exposure. Do not count both.
            if channel == "revenue" and sensitivity <= 0:
                return None
            if channel == "cost" and sensitivity >= 0:
                return None
            confidence = self._field_confidence(exposure, "commodity_exposures")
            used_confidences.append(confidence)
            return round(abs(factor_impact) * sensitivity * confidence, 4)

        fields = _FACTOR_EXPOSURE_MATRIX.get((factor, channel), ())
        effects = []
        for field_name in fields:
            value = getattr(exposure, field_name)
            if value is None:
                continue
            confidence = self._field_confidence(exposure, field_name)
            used_confidences.append(confidence)
            effects.append(factor_impact * value * confidence)
        if not effects:
            return None
        # FX revenue disclosures often overlap (foreign-currency and exports);
        # use the strongest evidenced measure instead of double counting.
        return round(max(effects, key=abs), 4)

    @staticmethod
    def _field_confidence(
        exposure: CompanyExposureSnapshot, field_name: str
    ) -> float:
        confidences = [
            item.confidence for item in exposure.field_evidence
            if item.field_name == field_name
        ]
        return max(confidences) if confidences else 0.0

    def _apply_modifier(
        self,
        component: float | None,
        modifier_name: str,
        exposure: CompanyExposureSnapshot,
    ) -> float | None:
        if component is None:
            return None
        modifier = getattr(exposure, modifier_name)
        if modifier is None:
            return component
        confidence = self._field_confidence(exposure, modifier_name)
        raw_multiplier = 0.7 + 0.3 * modifier
        confidence_adjusted = 1 + (raw_multiplier - 1) * confidence
        return round(component * confidence_adjusted, 4)

    @staticmethod
    def _combine(values: list[float]) -> float | None:
        if not values:
            return None
        return round(math.tanh(sum(values)), 4)
