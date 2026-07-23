"""Translate factor/channel relevance into auditable financial scenario deltas."""
from __future__ import annotations

from datetime import datetime
import hashlib

from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureSnapshot,
    CompanyImpactCandidate,
    FactorContribution,
)
from macro_b3_bot.domain.financial_bridge_models import (
    BlockedFinancialChannel,
    EconomicShockScenario,
    FinancialBaselineSnapshot,
    FinancialBridgeContribution,
    FinancialScenarioMetrics,
    FinancialScenarioOutcome,
)


_SHOCK_ASSUMPTIONS = {
    "LOW_SHOCK": {
        "INTEREST_RATES": 50.0,
        "INFLATION": 50.0,
        "FX": 5.0,
        "fx_pass_through": 0.25,
        "incremental_ebitda_margin": 0.20,
        "cash_conversion": 0.75,
    },
    "BASE_SHOCK": {
        "INTEREST_RATES": 100.0,
        "INFLATION": 100.0,
        "FX": 10.0,
        "fx_pass_through": 0.50,
        "incremental_ebitda_margin": 0.35,
        "cash_conversion": 0.85,
    },
    "HIGH_SHOCK": {
        "INTEREST_RATES": 200.0,
        "INFLATION": 200.0,
        "FX": 20.0,
        "fx_pass_through": 0.75,
        "incremental_ebitda_margin": 0.50,
        "cash_conversion": 0.95,
    },
}


class FinancialScenarioEngine:
    """Use causal scores as relevance/confidence only, never as percentages."""

    def __init__(
        self,
        run_id: str,
        methodology_version: str = "4D.2A-explicit-factor-direction-v1",
    ) -> None:
        self.run_id = run_id
        self.methodology_version = methodology_version

    def scenarios(
        self,
        as_of_timestamp: datetime,
        directions: dict[str, int] | None = None,
    ) -> list[EconomicShockScenario]:
        directions = directions or {}
        items: list[EconomicShockScenario] = []
        for shock_case, assumptions in _SHOCK_ASSUMPTIONS.items():
            for factor, unit in (
                ("FX", "PERCENT_CHANGE"),
                ("INTEREST_RATES", "BASIS_POINTS"),
                ("INFLATION", "BASIS_POINTS"),
            ):
                direction = 1 if directions.get(factor, 1) >= 0 else -1
                identity = (
                    f"{self.methodology_version}|{factor}|{shock_case}|"
                    f"{assumptions[factor]}|{direction}|"
                    f"{as_of_timestamp.isoformat()}"
                )
                items.append(EconomicShockScenario(
                    scenario_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                    factor=factor,
                    shock_case=shock_case,
                    direction=direction,
                    absolute_magnitude=assumptions[factor],
                    signed_magnitude=direction * assumptions[factor],
                    unit=unit,
                    horizon_years=1.0,
                    as_of_timestamp=as_of_timestamp,
                    premise_source="CONFIGURED_PILOT_ASSUMPTION",
                    assumption_ids=[
                        f"4D2_{factor}_{shock_case}",
                        "4D2_HORIZON_ONE_YEAR",
                    ],
                    methodology_version=self.methodology_version,
                ))
        return items

    def evaluate(
        self,
        baseline: FinancialBaselineSnapshot,
        exposure: CompanyExposureSnapshot,
        candidate: CompanyImpactCandidate,
    ) -> list[FinancialScenarioOutcome]:
        directions, conflicts = self.validated_factor_directions(
            candidate.factor_contributions
        )
        if conflicts:
            return self._conflicting_direction_outcomes(
                baseline, candidate, conflicts
            )
        scenarios = self.scenarios(candidate.as_of_timestamp, directions)
        by_shock_case = {
            shock_case: [
                item for item in scenarios if item.shock_case == shock_case
            ]
            for shock_case in ("LOW_SHOCK", "BASE_SHOCK", "HIGH_SHOCK")
        }
        raw = [
            self._case(
                baseline, exposure, candidate, shock_case, items, "BASE"
            )
            for shock_case, items in by_shock_case.items()
        ]
        ordered = sorted(
            raw,
            key=lambda item: (item.metrics.fcf, item.metrics.net_income),
        )
        labels = ("PESSIMISTIC", "BASE", "OPTIMISTIC")
        return [
            item.model_copy(update={
                "case": label,
                "outcome_id": self._outcome_id(
                    baseline, candidate, item.shock_case, label
                ),
            })
            for item, label in zip(ordered, labels, strict=True)
        ]

    def _case(
        self,
        baseline: FinancialBaselineSnapshot,
        exposure: CompanyExposureSnapshot,
        candidate: CompanyImpactCandidate,
        shock_case: str,
        scenarios: list[EconomicShockScenario],
        result_case: str,
    ) -> FinancialScenarioOutcome:
        baseline_metrics = self._baseline_metrics(baseline)
        if candidate.reason == "SECTOR_STATE_NO_ACTIVE_SIGNAL":
            return self._no_action(
                baseline, candidate, shock_case, result_case, baseline_metrics,
                "SECTOR_STATE_NO_ACTIVE_SIGNAL",
                [BlockedFinancialChannel(
                    factor="ALL", channel="all",
                    reason="BRIDGE_BLOCKED_NO_ACTIVE_SIGNAL",
                    required_fields=[],
                )],
            )

        scenario_by_factor = {item.factor: item for item in scenarios}
        contributions: list[FinancialBridgeContribution] = []
        blocked: list[BlockedFinancialChannel] = []
        seen: set[tuple[str, str, str]] = set()
        for factor_item in candidate.factor_contributions:
            key = (
                factor_item.factor, factor_item.channel,
                factor_item.exposure_field,
            )
            if key in seen:
                continue
            seen.add(key)
            scenario = scenario_by_factor.get(factor_item.factor)
            if scenario is None:
                blocked.append(self._blocked(
                    factor_item, "BRIDGE_BLOCKED_UNSUPPORTED_FACTOR_CHANNEL", []
                ))
                continue
            item, gap = self._bridge(
                baseline, exposure, candidate, factor_item, scenario, shock_case
            )
            if item:
                contributions.append(item)
            if gap:
                blocked.append(gap)
        for gap in [
            *candidate.missing_factor_exposures,
            *candidate.unsupported_factor_channels,
        ]:
            if gap.factor not in scenario_by_factor:
                continue
            if gap.channel in {"cost", "demand"}:
                reason = "BRIDGE_BLOCKED_MISSING_ELASTICITY"
            elif gap.factor == "FX" and gap.channel == "debt":
                reason = "BRIDGE_BLOCKED_MISSING_NET_EXPOSURE"
            else:
                reason = "BRIDGE_BLOCKED_MISSING_MONETARY_BASE"
            item = BlockedFinancialChannel(
                factor=gap.factor, channel=gap.channel, reason=reason,
                required_fields=gap.expected_fields,
            )
            if item not in blocked:
                blocked.append(item)
        active_pairs = {
            (item.factor, item.channel)
            for item in candidate.factor_contributions
        }
        inactive_capabilities = (
            (
                "INTEREST_RATES", "debt", baseline.average_floating_debt,
                ["average_floating_debt"],
            ),
            (
                "INFLATION", "debt", baseline.inflation_linked_debt,
                ["inflation_linked_debt"],
            ),
            (
                "FX", "debt", baseline.average_net_fx_debt,
                ["average_net_fx_debt"],
            ),
        )
        for factor_name, channel_name, value, required in inactive_capabilities:
            if value in {None, 0} or (factor_name, channel_name) in active_pairs:
                continue
            item = BlockedFinancialChannel(
                factor=factor_name,
                channel=channel_name,
                reason="BRIDGE_BLOCKED_NO_ACTIVE_CAUSAL_FACTOR",
                required_fields=required,
            )
            if item not in blocked:
                blocked.append(item)

        deltas = self._sum_deltas(contributions)
        metrics = FinancialScenarioMetrics(
            **{
                name: (
                    None if getattr(baseline_metrics, name) is None
                    else getattr(baseline_metrics, name) + getattr(deltas, name)
                )
                for name in FinancialScenarioMetrics.model_fields
            }
        )
        percentages = {
            name: self._percentage(
                getattr(deltas, name), getattr(baseline_metrics, name)
            )
            for name in FinancialScenarioMetrics.model_fields
        }
        margins = {
            "ebitda_margin": self._margin(metrics.ebitda, metrics.revenue),
            "ebit_margin": self._margin(metrics.ebit, metrics.revenue),
            "net_income_margin": self._margin(metrics.net_income, metrics.revenue),
            "fcf_margin": self._margin(metrics.fcf, metrics.revenue),
        }
        confidence = (
            sum(item.confidence for item in contributions) / len(contributions)
            if contributions else 0
        )
        status = "CALCULATED" if contributions and not blocked else (
            "PARTIAL" if contributions else "BLOCKED"
        )
        reason = (
            "FINANCIAL_BRIDGE_CALCULATED"
            if status == "CALCULATED"
            else "FINANCIAL_BRIDGE_PARTIAL"
            if status == "PARTIAL"
            else "NO_SUPPORTED_FINANCIAL_BRIDGE"
        )
        identity = (
            f"{self.run_id}|{baseline.baseline_id}|{candidate.candidate_id}|"
            f"{shock_case}|{result_case}"
        )
        return FinancialScenarioOutcome(
            outcome_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=baseline.ticker,
            case=result_case,
            shock_case=shock_case,
            as_of_timestamp=candidate.as_of_timestamp,
            baseline_id=baseline.baseline_id,
            company_impact_candidate_id=candidate.candidate_id,
            metrics=metrics,
            absolute_changes=deltas,
            percentage_changes=percentages,
            margins=margins,
            contributions=contributions,
            blocked_channels=blocked,
            confidence=round(confidence, 4),
            status=status,
            reason=reason,
            run_id=self.run_id,
        )

    def _bridge(
        self,
        baseline: FinancialBaselineSnapshot,
        exposure: CompanyExposureSnapshot,
        candidate: CompanyImpactCandidate,
        factor: FactorContribution,
        scenario: EconomicShockScenario,
        shock_case: str,
    ) -> tuple[
        FinancialBridgeContribution | None,
        BlockedFinancialChannel | None,
    ]:
        shock = scenario.signed_magnitude / (
            100 if scenario.unit == "PERCENT_CHANGE" else 10_000
        )
        assumptions = _SHOCK_ASSUMPTIONS[shock_case]
        tax_rate = baseline.effective_tax_rate
        tax_multiplier = 1 - tax_rate if tax_rate is not None else 1
        confidence = (
            baseline.confidence
            * factor.exposure_confidence
            * abs(factor.adjusted_factor_impact)
        )
        common = {
            "factor": factor.factor,
            "channel": factor.channel,
            "factor_direction": factor.factor_direction,
            "channel_effect_direction": factor.channel_effect_direction,
            "scenario_id": scenario.scenario_id,
            "source_candidate_id": candidate.candidate_id,
            "causal_direction": scenario.direction,
            "absolute_shock_magnitude": scenario.absolute_magnitude,
            "signed_shock_magnitude": scenario.signed_magnitude,
            "shock_unit": scenario.unit,
            "horizon_years": scenario.horizon_years,
            "confidence": round(confidence, 4),
            "causal_evidence_status": factor.evidence_status,
            "exposure_evidence_ids": factor.exposure_evidence_ids,
        }

        if factor.factor == "FX" and factor.channel == "revenue":
            exposure_value = self._first(
                exposure.revenue_foreign_currency_pct,
                exposure.export_revenue_pct,
            )
            if exposure_value is None:
                return None, self._blocked(
                    factor, "BRIDGE_BLOCKED_MISSING_MONETARY_BASE",
                    ["revenue_foreign_currency_pct", "export_revenue_pct"],
                )
            pass_through = assumptions["fx_pass_through"]
            delta_revenue = (
                baseline.ttm_revenue * exposure_value * shock * pass_through
            )
            incremental_margin = assumptions["incremental_ebitda_margin"]
            delta_ebitda = delta_revenue * incremental_margin
            delta_ebit = delta_ebitda
            delta_net = delta_ebit * tax_multiplier
            cash_conversion = assumptions["cash_conversion"]
            delta_fcf = delta_net * cash_conversion
            return FinancialBridgeContribution(
                **common,
                bridge_type="FX_REVENUE_TRANSLATION",
                exposure_field=(
                    "revenue_foreign_currency_pct"
                    if exposure.revenue_foreign_currency_pct is not None
                    else "export_revenue_pct"
                ),
                exposure_value=exposure_value,
                monetary_base_field="ttm_revenue",
                monetary_base_value=baseline.ttm_revenue,
                formula=(
                    "ttm_revenue * exposed_revenue_pct * fx_change "
                    "* fx_pass_through"
                ),
                assumptions={
                    "fx_pass_through": pass_through,
                    "incremental_ebitda_margin": incremental_margin,
                    "cash_conversion": cash_conversion,
                    "tax_rate": tax_rate or 0,
                },
                delta_revenue=delta_revenue,
                delta_ebitda=delta_ebitda,
                delta_ebit=delta_ebit,
                delta_pre_tax_income=delta_ebit,
                delta_net_income=delta_net,
                delta_operating_cash_flow=delta_fcf,
                delta_fcf=delta_fcf,
                delta_net_debt=-delta_fcf,
                baseline_evidence_ids=self._baseline_sources(
                    baseline, "ttm_revenue"
                ),
            ), None

        if factor.factor == "FX" and factor.channel == "debt":
            if baseline.average_net_fx_debt is None:
                return None, self._blocked(
                    factor, "BRIDGE_BLOCKED_MISSING_NET_EXPOSURE",
                    ["average_net_fx_debt", "net_foreign_currency_debt_pct"],
                )
            delta_financial = -baseline.average_net_fx_debt * shock
            delta_net = delta_financial * tax_multiplier
            return FinancialBridgeContribution(
                **common,
                bridge_type="NET_FX_DEBT_REVALUATION",
                exposure_field="net_foreign_currency_debt_pct",
                exposure_value=exposure.net_foreign_currency_debt_pct or 0,
                monetary_base_field="average_net_fx_debt",
                monetary_base_value=baseline.average_net_fx_debt,
                formula="average_net_fx_debt * -fx_change",
                assumptions={"tax_rate": tax_rate or 0},
                delta_financial_result=delta_financial,
                accounting_fx_revaluation=delta_financial,
                delta_pre_tax_income=delta_financial,
                delta_net_income=delta_net,
                delta_operating_cash_flow=0,
                delta_fcf=0,
                delta_net_debt=0,
                baseline_evidence_ids=self._baseline_sources(
                    baseline, "average_net_fx_debt"
                ),
            ), None

        if factor.factor == "INTEREST_RATES" and factor.channel == "debt":
            if baseline.average_floating_debt is None:
                return None, self._blocked(
                    factor, "BRIDGE_BLOCKED_MISSING_MONETARY_BASE",
                    [
                        "average_floating_debt",
                        "post_hedge_floating_rate_debt_pct",
                    ],
                )
            delta_financial = (
                -baseline.average_floating_debt * shock * scenario.horizon_years
            )
            delta_net = delta_financial * tax_multiplier
            return FinancialBridgeContribution(
                **common,
                bridge_type="FLOATING_RATE_DEBT",
                exposure_field="post_hedge_floating_rate_debt_pct",
                exposure_value=exposure.post_hedge_floating_rate_debt_pct or 0,
                monetary_base_field="average_floating_debt",
                monetary_base_value=baseline.average_floating_debt,
                formula=(
                    "average_floating_debt * -(rate_bps / 10000) "
                    "* horizon_years"
                ),
                assumptions={"tax_rate": tax_rate or 0},
                delta_financial_result=delta_financial,
                cash_interest_effect=delta_financial,
                delta_pre_tax_income=delta_financial,
                delta_net_income=delta_net,
                delta_operating_cash_flow=delta_net,
                delta_fcf=delta_net,
                delta_net_debt=-delta_net,
                baseline_evidence_ids=self._baseline_sources(
                    baseline, "average_floating_debt"
                ),
            ), None

        if factor.factor == "INFLATION" and factor.channel == "debt":
            if baseline.inflation_linked_debt is None:
                return None, self._blocked(
                    factor, "BRIDGE_BLOCKED_MISSING_MONETARY_BASE",
                    ["inflation_linked_debt", "inflation_linked_debt_pct"],
                )
            delta_financial = (
                -baseline.inflation_linked_debt * shock * scenario.horizon_years
            )
            delta_net = delta_financial * tax_multiplier
            return FinancialBridgeContribution(
                **common,
                bridge_type="INFLATION_LINKED_DEBT",
                exposure_field="inflation_linked_debt_pct",
                exposure_value=exposure.inflation_linked_debt_pct or 0,
                monetary_base_field="inflation_linked_debt",
                monetary_base_value=baseline.inflation_linked_debt,
                formula=(
                    "inflation_linked_debt * -(inflation_bps / 10000) "
                    "* horizon_years"
                ),
                assumptions={"tax_rate": tax_rate or 0},
                delta_financial_result=delta_financial,
                cash_interest_effect=delta_financial,
                delta_pre_tax_income=delta_financial,
                delta_net_income=delta_net,
                delta_operating_cash_flow=delta_net,
                delta_fcf=delta_net,
                delta_net_debt=-delta_net,
                baseline_evidence_ids=self._baseline_sources(
                    baseline, "inflation_linked_debt"
                ),
            ), None

        if factor.channel in {"cost", "demand"}:
            return None, self._blocked(
                factor,
                "BRIDGE_BLOCKED_MISSING_ELASTICITY",
                [f"{factor.factor}_{factor.channel}_elasticity"],
            )
        return None, self._blocked(
            factor, "BRIDGE_BLOCKED_UNSUPPORTED_FACTOR_CHANNEL", []
        )

    def _no_action(
        self,
        baseline: FinancialBaselineSnapshot,
        candidate: CompanyImpactCandidate,
        shock_case: str,
        result_case: str,
        metrics: FinancialScenarioMetrics,
        reason: str,
        blocked: list[BlockedFinancialChannel],
    ) -> FinancialScenarioOutcome:
        zero = FinancialScenarioMetrics(
            revenue=0, ebitda=0 if metrics.ebitda is not None else None,
            ebit=0, financial_result=0, pre_tax_income=0, net_income=0,
            operating_cash_flow=0, fcf=0, net_debt=0,
        )
        identity = (
            f"{self.run_id}|{baseline.baseline_id}|{candidate.candidate_id}|"
            f"{shock_case}|{result_case}"
        )
        return FinancialScenarioOutcome(
            outcome_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=baseline.ticker, case=result_case, shock_case=shock_case,
            as_of_timestamp=candidate.as_of_timestamp,
            baseline_id=baseline.baseline_id,
            company_impact_candidate_id=candidate.candidate_id,
            metrics=metrics, absolute_changes=zero,
            percentage_changes={
                name: 0 for name in FinancialScenarioMetrics.model_fields
            },
            margins={
                "ebitda_margin": self._margin(metrics.ebitda, metrics.revenue),
                "ebit_margin": self._margin(metrics.ebit, metrics.revenue),
                "net_income_margin": self._margin(metrics.net_income, metrics.revenue),
                "fcf_margin": self._margin(metrics.fcf, metrics.revenue),
            },
            contributions=[], blocked_channels=blocked, confidence=0,
            status="NO_ACTION", reason=reason, run_id=self.run_id,
        )

    def _outcome_id(
        self,
        baseline: FinancialBaselineSnapshot,
        candidate: CompanyImpactCandidate,
        shock_case: str,
        result_case: str,
    ) -> str:
        identity = (
            f"{self.run_id}|{baseline.baseline_id}|{candidate.candidate_id}|"
            f"{shock_case}|{result_case}"
        )
        return hashlib.sha256(identity.encode()).hexdigest()[:24]

    @staticmethod
    def validated_factor_directions(
        factors: list[FactorContribution],
    ) -> tuple[dict[str, int], list[str]]:
        observed: dict[str, set[int]] = {}
        for item in factors:
            observed.setdefault(item.factor, set()).add(item.factor_direction)
        conflicts = sorted(
            factor for factor, directions in observed.items()
            if len(directions) > 1
        )
        return (
            {
                factor: next(iter(directions))
                for factor, directions in observed.items()
                if len(directions) == 1
            },
            conflicts,
        )

    def _conflicting_direction_outcomes(
        self,
        baseline: FinancialBaselineSnapshot,
        candidate: CompanyImpactCandidate,
        conflicting_factors: list[str],
    ) -> list[FinancialScenarioOutcome]:
        metrics = self._baseline_metrics(baseline)
        outcomes: list[FinancialScenarioOutcome] = []
        for shock_case, result_case in zip(
            ("LOW_SHOCK", "BASE_SHOCK", "HIGH_SHOCK"),
            ("PESSIMISTIC", "BASE", "OPTIMISTIC"),
            strict=True,
        ):
            blocked = [
                BlockedFinancialChannel(
                    factor=factor,
                    channel="all",
                    reason="SCENARIO_BLOCKED_CONFLICTING_FACTOR_DIRECTION",
                    required_fields=["factor_direction"],
                )
                for factor in conflicting_factors
            ]
            item = self._no_action(
                baseline,
                candidate,
                shock_case,
                result_case,
                metrics,
                "SCENARIO_BLOCKED_CONFLICTING_FACTOR_DIRECTION",
                blocked,
            )
            outcomes.append(item.model_copy(update={"status": "BLOCKED"}))
        return outcomes

    @staticmethod
    def _baseline_metrics(
        baseline: FinancialBaselineSnapshot,
    ) -> FinancialScenarioMetrics:
        return FinancialScenarioMetrics(
            revenue=baseline.ttm_revenue,
            ebitda=baseline.ttm_ebitda,
            ebit=baseline.ttm_ebit,
            financial_result=baseline.ttm_financial_result,
            pre_tax_income=baseline.ttm_pre_tax_income,
            net_income=baseline.ttm_net_income,
            operating_cash_flow=baseline.ttm_operating_cash_flow,
            fcf=baseline.ttm_fcf,
            net_debt=baseline.net_debt,
        )

    @staticmethod
    def _sum_deltas(
        items: list[FinancialBridgeContribution],
    ) -> FinancialScenarioMetrics:
        return FinancialScenarioMetrics(
            revenue=sum(item.delta_revenue for item in items),
            ebitda=sum(item.delta_ebitda for item in items),
            ebit=sum(item.delta_ebit for item in items),
            financial_result=sum(item.delta_financial_result for item in items),
            pre_tax_income=sum(item.delta_pre_tax_income for item in items),
            net_income=sum(item.delta_net_income for item in items),
            operating_cash_flow=sum(item.delta_operating_cash_flow for item in items),
            fcf=sum(item.delta_fcf for item in items),
            net_debt=sum(item.delta_net_debt for item in items),
        )

    @staticmethod
    def _blocked(
        factor: FactorContribution, reason: str, required: list[str]
    ) -> BlockedFinancialChannel:
        return BlockedFinancialChannel(
            factor=factor.factor, channel=factor.channel,
            reason=reason, required_fields=required,
        )

    @staticmethod
    def _first(*values: float | None) -> float | None:
        return next((value for value in values if value is not None), None)

    @staticmethod
    def _percentage(delta: float | None, baseline: float | None) -> float | None:
        if delta is None or baseline in {None, 0}:
            return None
        return delta / abs(baseline)

    @staticmethod
    def _margin(value: float | None, revenue: float) -> float | None:
        return value / revenue if value is not None and revenue else None

    @staticmethod
    def _baseline_sources(
        baseline: FinancialBaselineSnapshot, field: str
    ) -> list[str]:
        return sorted({
            source
            for item in baseline.field_evidence if item.field_name == field
            for source in item.source_ids
        })
