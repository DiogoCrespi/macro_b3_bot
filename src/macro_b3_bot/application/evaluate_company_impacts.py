"""Auditable factor-specific company impact without valuation or BUY."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime

from macro_b3_bot.domain.causal_models import SectorStateSnapshot
from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureSnapshot,
    CompanyFactorChannel,
    CompanyImpactCandidate,
    FactorContribution,
    MissingFactorExposure,
)


MODIFIER_METHODOLOGY_VERSION = "company-modifier-neutral-beta-v1"
MODIFIER_BETA = 0.5
HYPOTHESIS_WEIGHT = 0.4

_FACTOR_EXPOSURE_MATRIX: dict[tuple[str, str], tuple[str, ...]] = {
    ("FX", "revenue"): ("revenue_foreign_currency_pct", "export_revenue_pct"),
    ("FX", "cost"): ("cost_foreign_currency_pct",),
    ("FX", "debt"): ("foreign_currency_debt_pct",),
    ("INTEREST_RATES", "debt"): ("floating_rate_debt_pct",),
    ("INFLATION", "debt"): ("inflation_linked_debt_pct",),
    ("ECONOMIC_ACTIVITY", "demand"): ("demand_cyclicality",),
}


class CompanyImpactEngine:
    """Apply each factor only to its mapped, field-evidenced exposure."""

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
        null_sector_state = sector.status in {
            "SECTOR_STATE_NO_ACTIVE_SIGNAL",
            "SECTOR_STATE_NO_GRAPH_COVERAGE",
            "SECTOR_STATE_MISSING_DATA",
        }
        inputs = [] if null_sector_state else self._inputs(
            factor_impacts, factor_channels
        )
        contributions: list[FactorContribution] = []
        missing: list[MissingFactorExposure] = []
        unsupported: list[MissingFactorExposure] = []
        for item, causal_impact, evidence_weight, adjusted_impact in inputs:
            produced, gap = self._factor_contributions(
                item, causal_impact, evidence_weight, adjusted_impact, exposure
            )
            contributions.extend(produced)
            if gap:
                (unsupported if gap.reason == "NO_EXPOSURE_MAPPING" else missing).append(gap)

        by_channel: dict[str, list[float]] = {
            "revenue": [], "cost": [], "debt": [], "demand": [],
        }
        for item in contributions:
            by_channel[item.channel].append(item.final_contribution)
        components = {
            channel: self._combine(values) for channel, values in by_channel.items()
        }
        missing_components = [
            channel for channel, value in components.items() if value is None
        ]
        known = [value for value in components.values() if value is not None]
        net = math.tanh(sum(known)) if known else None
        field_quality = (
            sum(item.exposure_confidence for item in contributions) / len(contributions)
            if contributions else 0.0
        )
        confidence = field_quality * sector.confidence * (len(known) / 4)
        evidence_status = (
            "HYPOTHESIS"
            if not inputs or any(
                item.evidence_status == "HYPOTHESIS" for item, *_ in inputs
            )
            else "VALIDATED"
        )
        status = "WATCH" if len(known) >= 3 and confidence >= 0.5 else "NO_ACTION"
        if evidence_status == "HYPOTHESIS" and status not in {"WATCH", "NO_ACTION"}:
            status = "WATCH"
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
            supporting_event_ids=sector.supporting_event_ids,
            opposing_event_ids=sector.opposing_event_ids,
            source_path_ids=sorted({
                path_id for item in contributions for path_id in item.source_path_ids
            }),
            causal_edge_ids=sorted({
                edge_id for item in contributions for edge_id in item.causal_edge_ids
            }),
            factor_contributions=contributions,
            missing_factor_exposures=missing,
            unsupported_factor_channels=unsupported,
            causal_evidence_status=evidence_status,
            missing_exposures=missing_components,
            status=status,
            reason=sector.status if null_sector_state else None,
            run_id=self.run_id,
        )

    @staticmethod
    def _inputs(
        factor_impacts: dict[tuple[str, str], float] | None,
        channels: list[CompanyFactorChannel] | None,
    ) -> list[tuple[CompanyFactorChannel, float, float, float]]:
        if channels:
            return [
                (
                    item,
                    item.direction * item.strength * item.confidence,
                    1.0 if item.evidence_status == "VALIDATED" else HYPOTHESIS_WEIGHT,
                    item.direction * item.strength * item.confidence
                    * (1.0 if item.evidence_status == "VALIDATED" else HYPOTHESIS_WEIGHT),
                )
                for item in channels
            ]
        return [
            (
                CompanyFactorChannel(
                    factor=factor, channel=channel,
                    direction=1 if impact >= 0 else -1,
                    strength=abs(impact), confidence=1,
                    source_path_ids=["DIRECT_FACTOR_INPUT"],
                    causal_edge_ids=["DIRECT_FACTOR_INPUT"],
                    evidence_ids=[], evidence_status="HYPOTHESIS",
                ),
                impact,
                HYPOTHESIS_WEIGHT,
                impact * HYPOTHESIS_WEIGHT,
            )
            for (factor, channel), impact in (factor_impacts or {}).items()
        ]

    def _factor_contributions(
        self,
        channel: CompanyFactorChannel,
        causal_impact: float,
        evidence_weight: float,
        adjusted_impact: float,
        exposure: CompanyExposureSnapshot,
    ) -> tuple[list[FactorContribution], MissingFactorExposure | None]:
        factor, component = channel.factor, channel.channel
        if factor in {"OIL", "COMMODITY"} and component in {"revenue", "cost"}:
            sensitivity = (exposure.commodity_exposures or {}).get("OIL")
            expected = ["commodity_exposures.OIL"]
            if sensitivity is None:
                return [], self._gap(factor, component, "MISSING_EXPOSURE_VALUE", expected)
            if component == "revenue" and sensitivity <= 0:
                return [], None
            if component == "cost" and sensitivity >= 0:
                return [], None
            return [self._contribution(
                channel, causal_impact, evidence_weight, adjusted_impact,
                "commodity_exposures", abs(sensitivity), exposure,
                self._modifier(factor, component, exposure),
            )], None

        fields = _FACTOR_EXPOSURE_MATRIX.get((factor, component))
        if fields is None:
            return [], self._gap(
                factor, component, "NO_EXPOSURE_MAPPING", []
            )
        available = [
            field for field in fields if getattr(exposure, field) is not None
        ]
        if not available:
            return [], self._gap(
                factor, component, "MISSING_EXPOSURE_VALUE", list(fields)
            )
        # Overlapping FX revenue disclosures must not be double counted.
        selected = max(
            available,
            key=lambda field: abs(
                getattr(exposure, field) * self._field_confidence(exposure, field)
            ),
        )
        return [self._contribution(
            channel, causal_impact, evidence_weight, adjusted_impact,
            selected, getattr(exposure, selected), exposure,
            self._modifier(factor, component, exposure),
        )], None

    def _contribution(
        self,
        channel: CompanyFactorChannel,
        causal_impact: float,
        evidence_weight: float,
        adjusted_impact: float,
        field_name: str,
        field_value: float,
        exposure: CompanyExposureSnapshot,
        modifier: tuple[str, float, float] | None,
    ) -> FactorContribution:
        exposure_confidence = self._field_confidence(exposure, field_name)
        value = adjusted_impact * field_value * exposure_confidence
        modifier_fields: list[str] = []
        if modifier:
            modifier_name, multiplier, _ = modifier
            value *= multiplier
            modifier_fields.append(modifier_name)
        return FactorContribution(
            factor=channel.factor, channel=channel.channel,
            causal_factor_impact=round(causal_impact, 4),
            evidence_weight=evidence_weight,
            adjusted_factor_impact=round(adjusted_impact, 4),
            exposure_field=field_name, exposure_value=field_value,
            exposure_confidence=exposure_confidence,
            modifier_fields=modifier_fields,
            modifier_methodology_version=(
                MODIFIER_METHODOLOGY_VERSION if modifier else None
            ),
            modifier_beta=MODIFIER_BETA if modifier else None,
            final_contribution=round(value, 4),
            source_path_ids=channel.source_path_ids,
            causal_edge_ids=channel.causal_edge_ids,
            evidence_ids=channel.evidence_ids,
            evidence_status=channel.evidence_status,
        )

    def _modifier(
        self,
        factor: str,
        channel: str,
        exposure: CompanyExposureSnapshot,
    ) -> tuple[str, float, float] | None:
        modifier_name = None
        if factor == "INFLATION" and channel == "revenue":
            modifier_name = "pricing_power"
        elif factor in {"INFLATION", "OIL", "COMMODITY"} and channel == "cost":
            modifier_name = "operating_leverage"
        if modifier_name is None:
            return None
        value = getattr(exposure, modifier_name)
        if value is None:
            return None
        confidence = self._field_confidence(exposure, modifier_name)
        multiplier = 1.0 + MODIFIER_BETA * (value - 0.5) * confidence
        return modifier_name, multiplier, confidence

    @staticmethod
    def _field_confidence(
        exposure: CompanyExposureSnapshot, field_name: str
    ) -> float:
        values = [
            item.confidence for item in exposure.field_evidence
            if item.field_name == field_name
        ]
        return max(values) if values else 0.0

    @staticmethod
    def _gap(
        factor: str, channel: str, reason: str, fields: list[str]
    ) -> MissingFactorExposure:
        return MissingFactorExposure(
            factor=factor, channel=channel, reason=reason,
            expected_fields=fields,
        )

    @staticmethod
    def _combine(values: list[float]) -> float | None:
        return round(math.tanh(sum(values)), 4) if values else None
