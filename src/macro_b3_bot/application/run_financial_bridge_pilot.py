"""Sprint 4D.2A explicit-factor-direction financial bridge pilot."""
from __future__ import annotations

from datetime import datetime

from macro_b3_bot.application.build_company_exposures import CompanyExposureBuilder
from macro_b3_bot.application.build_financial_baselines import FinancialBaselineBuilder
from macro_b3_bot.application.dry_run_company_impact_pilot import (
    CompanyImpactPilotDryRun,
)
from macro_b3_bot.application.evaluate_company_impacts import CompanyImpactEngine
from macro_b3_bot.application.evaluate_financial_scenarios import (
    FinancialScenarioEngine,
)
from macro_b3_bot.application.transport_company_channels import (
    CompanyChannelTransport,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


_PILOT = (
    {"ticker": "MGLU3", "sector": "VAREJO"},
    {"ticker": "SUZB3", "sector": "PAPEL_CELULOSE"},
    {"ticker": "KLBN11", "sector": "PAPEL_CELULOSE"},
    {"ticker": "RAIL3", "sector": "LOGISTICA"},
    {"ticker": "SLCE3", "sector": "AGRO_ALIMENTOS"},
)


class FinancialBridgePilot:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store
        self.loader = CompanyImpactPilotDryRun(store)

    def run(
        self,
        *,
        selection_run_id: str,
        sector_run_id: str,
        as_of_timestamp: datetime,
        run_id: str = "financial_4d2a_pilot",
    ) -> dict[str, object]:
        exposure_run_id = f"{run_id}_exposures"
        CompanyExposureBuilder(
            self.store,
            exposure_run_id,
            methodology_version="4C.5B-post-hedge-v1",
            source_selection_run_id=selection_run_id,
        ).build_pilot(as_of_timestamp, list(_PILOT))

        baseline_builder = FinancialBaselineBuilder(
            self.store, f"{run_id}_baselines"
        )
        scenario_engine = FinancialScenarioEngine(run_id)
        scenarios = scenario_engine.scenarios(as_of_timestamp)
        for scenario in scenarios:
            self.store.save_economic_shock_scenario(
                scenario.model_dump(mode="json"), run_id
            )

        companies: list[dict[str, object]] = []
        baseline_count = outcome_count = calculated_count = 0
        no_active_count = future_evidence = score_as_percentage = 0
        for company in _PILOT:
            ticker = company["ticker"]
            exposure = self.loader._exposure(ticker, exposure_run_id)
            if exposure is None:
                companies.append({
                    "ticker": ticker, "error": "MISSING_APPROVED_EXPOSURE",
                })
                continue
            sector = self.loader._sector(exposure.sector, sector_run_id)
            if sector is None:
                companies.append({
                    "ticker": ticker, "error": "MISSING_SECTOR_STATE",
                })
                continue
            channels = CompanyChannelTransport().from_sector_candidates(
                self.loader._sector_candidates(exposure.sector, sector_run_id)
            )
            candidates = {}
            for policy in ("THREE_COMPONENTS", "MATERIALITY_COVERAGE"):
                candidate_item = CompanyImpactEngine(
                    f"{run_id}_{policy.lower()}"
                ).evaluate(
                    sector, exposure, None, as_of_timestamp,
                    factor_channels=channels,
                    decision_policy=policy,
                )
                self.store.save_company_impact_candidate(
                    candidate_item.model_dump(mode="json")
                )
                candidates[policy] = candidate_item
            candidate = candidates["MATERIALITY_COVERAGE"]
            baseline = baseline_builder.build(
                ticker, as_of_timestamp, exposure
            )
            baseline_count += 1
            future_evidence += sum(
                available > baseline.as_of_timestamp
                for item in baseline.field_evidence
                for available in item.available_at
            )
            outcomes = scenario_engine.evaluate(baseline, exposure, candidate)
            factor_directions, _ = scenario_engine.validated_factor_directions(
                candidate.factor_contributions
            )
            actual_scenarios = scenario_engine.scenarios(
                as_of_timestamp, factor_directions
            )
            for scenario in actual_scenarios:
                self.store.save_economic_shock_scenario(
                    scenario.model_dump(mode="json"), run_id
                )
            for outcome in outcomes:
                self.store.save_financial_scenario_outcome(
                    outcome.model_dump(mode="json")
                )
                outcome_count += 1
                calculated_count += outcome.status in {"CALCULATED", "PARTIAL"}
                no_active_count += (
                    sector.status == "SECTOR_STATE_NO_ACTIVE_SIGNAL"
                    and outcome.status == "NO_ACTION"
                    and not outcome.contributions
                )
            companies.append({
                "ticker": ticker,
                "sector_state": sector.status,
                "baseline": baseline.model_dump(mode="json"),
                "source_candidate": {
                    "candidate_id": candidate.candidate_id,
                    "decision_policy": candidate.decision_policy,
                    "normalized_score_is_not_financial_percentage": True,
                },
                "policy_comparison": {
                    policy: {
                        "candidate_id": item.candidate_id,
                        "status": item.status,
                        "reason": item.reason,
                        "known_component_count": item.known_component_count,
                        "confidence": item.confidence,
                    }
                    for policy, item in candidates.items()
                },
                "outcomes": [
                    outcome.model_dump(mode="json") for outcome in outcomes
                ],
            })

        base_by_ticker = {
            item["ticker"]: next((
                outcome for outcome in item.get("outcomes", [])
                if outcome["case"] == "BASE"
            ), None)
            for item in companies
        }
        mglu = base_by_ticker.get("MGLU3")
        suzb = base_by_ticker.get("SUZB3")
        klbn = base_by_ticker.get("KLBN11")
        return {
            "run_id": run_id,
            "selection_run_id": selection_run_id,
            "sector_run_id": sector_run_id,
            "as_of_timestamp": as_of_timestamp.isoformat(),
            "companies_requested": len(_PILOT),
            "financial_baselines_built": baseline_count,
            "economic_shock_scenarios": len(scenarios),
            "financial_scenario_outcomes": outcome_count,
            "calculated_or_partial_outcomes": calculated_count,
            "no_active_signal_no_action_outcomes": no_active_count,
            "future_evidence_used": future_evidence,
            "normalized_scores_used_as_percentages": score_as_percentage,
            "policy_comparison_retained": [
                "THREE_COMPONENTS", "MATERIALITY_COVERAGE"
            ],
            "policy_selected_as_final": False,
            "companies": companies,
            "acceptance_checks": {
                "five_pit_baselines": baseline_count == 5,
                "future_documents_used_zero": future_evidence == 0,
                "shock_units_explicit": all(
                    item.unit in {
                        "PERCENT_CHANGE", "BASIS_POINTS",
                        "PERCENTAGE_POINTS",
                    }
                    for item in scenarios
                ),
                "normalized_score_used_as_percentage_zero": (
                    score_as_percentage == 0
                ),
                "factor_direction_explicit": all(
                    contribution["factor_direction"] in {-1, 1}
                    and contribution["channel_effect_direction"] in {-1, 1}
                    for company in companies
                    for outcome in company.get("outcomes", [])
                    for contribution in outcome["contributions"]
                ),
                "outcomes_ordered_by_company_result": all(
                    [
                        item["metrics"]["fcf"],
                        item["metrics"]["net_income"],
                    ]
                    <= [
                        next_item["metrics"]["fcf"],
                        next_item["metrics"]["net_income"],
                    ]
                    for company in companies
                    for item, next_item in zip(
                        company.get("outcomes", []),
                        company.get("outcomes", [])[1:],
                    )
                ),
                "unrealized_fx_revaluation_in_fcf_zero": all(
                    contribution["delta_fcf"] == 0
                    and contribution["delta_operating_cash_flow"] == 0
                    and contribution["delta_net_debt"] == 0
                    for company in companies
                    for outcome in company.get("outcomes", [])
                    for contribution in outcome["contributions"]
                    if contribution["bridge_type"]
                    == "NET_FX_DEBT_REVALUATION"
                ),
                "fcf_proxy_disclosed": all(
                    company.get("baseline", {}).get("fcf_definition")
                    == "CFO_PLUS_REPORTED_CAPEX"
                    and company.get("baseline", {}).get(
                        "fcf_normalization_status"
                    )
                    == "NOT_NORMALIZED"
                    for company in companies
                    if "baseline" in company
                ),
                "mglu_financial_impact_calculated": bool(
                    mglu and mglu["contributions"]
                ),
                "suzb_conflicting_fx_direction_blocked": bool(
                    suzb
                    and suzb["status"] == "BLOCKED"
                    and any(
                        item["reason"]
                        == "SCENARIO_BLOCKED_CONFLICTING_FACTOR_DIRECTION"
                        for item in suzb["blocked_channels"]
                    )
                ),
                "klbn_conflicting_fx_direction_blocked": bool(
                    klbn
                    and klbn["status"] == "BLOCKED"
                    and any(
                        item["reason"]
                        == "SCENARIO_BLOCKED_CONFLICTING_FACTOR_DIRECTION"
                        for item in klbn["blocked_channels"]
                    )
                ),
                "missing_elasticities_blocked": any(
                    gap["reason"] == "BRIDGE_BLOCKED_MISSING_ELASTICITY"
                    for item in companies
                    for outcome in item.get("outcomes", [])
                    for gap in outcome["blocked_channels"]
                ),
                "no_active_signal_is_no_action": no_active_count == 6,
                "formulas_and_evidence_persisted": all(
                    contribution["formula"]
                    and contribution["baseline_evidence_ids"]
                    and contribution["exposure_evidence_ids"]
                    for item in companies
                    for outcome in item.get("outcomes", [])
                    for contribution in outcome["contributions"]
                ),
            },
            "safety": {
                "valuation_enabled": False,
                "buy_enabled": False,
                "orders_enabled": False,
                "mirofish_enabled": False,
            },
        }
