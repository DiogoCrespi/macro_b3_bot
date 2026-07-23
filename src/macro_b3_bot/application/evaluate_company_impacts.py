"""Combine sector state and evidenced company exposures without valuation or BUY."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from statistics import mean

from macro_b3_bot.domain.causal_models import SectorStateSnapshot
from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureSnapshot,
    CompanyFactorChannel,
    CompanyImpactCandidate,
)
from macro_b3_bot.application.transport_company_channels import CompanyChannelTransport


class CompanyImpactEngine:
    """
    Factor signs must be supplied explicitly by the causal path layer.

    This avoids pretending that a generic positive sector score identifies whether
    FX, rates, commodities, or demand caused revenue, cost, and debt effects.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def evaluate(
        self,
        sector: SectorStateSnapshot,
        exposure: CompanyExposureSnapshot,
        factor_impacts: dict[str, float] | None,
        as_of_timestamp: datetime,
        factor_channels: list[CompanyFactorChannel] | None = None,
    ) -> CompanyImpactCandidate:
        if sector.sector != exposure.sector:
            raise ValueError("sector snapshot and company exposure do not match")
        impacts = factor_impacts or CompanyChannelTransport.aggregate(
            factor_channels or []
        )
        components = {
            "revenue": self._component(
                impacts.get("revenue"),
                [exposure.revenue_foreign_currency_pct, exposure.export_revenue_pct, exposure.pricing_power],
                exposure.confidence,
            ),
            "cost": self._component(
                impacts.get("cost"),
                [exposure.cost_foreign_currency_pct, exposure.operating_leverage],
                exposure.confidence,
            ),
            "debt": self._component(
                impacts.get("debt"),
                [exposure.foreign_currency_debt_pct, exposure.floating_rate_debt_pct,
                 exposure.inflation_linked_debt_pct],
                exposure.confidence,
            ),
            "demand": self._component(
                impacts.get("demand"), [exposure.demand_cyclicality], exposure.confidence
            ),
        }
        missing = [name for name, value in components.items() if value is None]
        known = [value for value in components.values() if value is not None]
        net = math.tanh(sum(known)) if known else None
        confidence = exposure.confidence * sector.confidence * (len(known) / 4)
        status = "WATCH" if len(known) >= 3 and confidence >= 0.5 else "NO_ACTION"
        identity = (
            f"{self.run_id}|{sector.snapshot_id}|{exposure.exposure_id}|"
            f"{as_of_timestamp.isoformat()}"
        )
        return CompanyImpactCandidate(
            candidate_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=exposure.ticker, sector_snapshot_id=sector.snapshot_id,
            company_exposure_id=exposure.exposure_id, as_of_timestamp=as_of_timestamp,
            revenue_impact_score=components["revenue"], cost_impact_score=components["cost"],
            debt_impact_score=components["debt"], demand_impact_score=components["demand"],
            net_company_impact=round(net, 4) if net is not None else None,
            confidence=round(confidence, 4), conflict_ratio=sector.conflict_ratio,
            supporting_paths=sector.supporting_event_ids,
            opposing_paths=sector.opposing_event_ids, missing_exposures=missing,
            status=status, run_id=self.run_id,
        )

    @staticmethod
    def _component(
        factor_impact: float | None,
        sensitivities: list[float | None],
        exposure_confidence: float,
    ) -> float | None:
        known = [value for value in sensitivities if value is not None]
        if factor_impact is None or not known:
            return None
        if factor_impact < -1 or factor_impact > 1:
            raise ValueError("factor impacts must be between -1 and 1")
        return round(factor_impact * mean(known) * exposure_confidence, 4)
