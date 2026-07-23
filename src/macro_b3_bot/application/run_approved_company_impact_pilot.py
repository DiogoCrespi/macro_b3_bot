"""Five-company approved exposure-to-impact pilot for Sprint 4C freeze."""
from __future__ import annotations

from datetime import datetime
import json

from macro_b3_bot.application.build_company_exposures import CompanyExposureBuilder
from macro_b3_bot.application.dry_run_company_impact_pilot import (
    CompanyImpactPilotDryRun,
)
from macro_b3_bot.application.evaluate_company_impacts import CompanyImpactEngine
from macro_b3_bot.application.transport_company_channels import (
    CompanyChannelTransport,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


_PILOT = (
    {"ticker": "SUZB3", "sector": "PAPEL_CELULOSE"},
    {"ticker": "KLBN11", "sector": "PAPEL_CELULOSE"},
    {"ticker": "MGLU3", "sector": "VAREJO"},
    {"ticker": "RAIL3", "sector": "LOGISTICA"},
    {"ticker": "SLCE3", "sector": "AGRO_ALIMENTOS"},
)


class ApprovedCompanyImpactPilot:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store
        self.loader = CompanyImpactPilotDryRun(store)

    def run(
        self,
        *,
        selection_run_id: str,
        sector_run_id: str,
        as_of_timestamp: datetime,
        exposure_run_id: str = "exposure_4c5b_approved_pilot",
    ) -> dict[str, object]:
        build = CompanyExposureBuilder(
            self.store,
            exposure_run_id,
            methodology_version="4C.5B-post-hedge-v1",
            source_selection_run_id=selection_run_id,
        ).build_pilot(as_of_timestamp, list(_PILOT))

        comparisons: list[dict[str, object]] = []
        written = inserted = 0
        unapproved_inputs: list[dict[str, str]] = []
        calculable_tickers: set[str] = set()
        no_active_signal_pass = False
        fact_rows = self.store.connection.execute(
            """
            SELECT ticker,field_name,evidence_payload,review_status
              FROM company_macro_exposure_facts
             WHERE selection_run_id=? AND is_active=TRUE
            """,
            [selection_run_id],
        ).fetchall()
        fact_status = {
            (ticker, field_name, json.loads(payload)["evidence_id"]): status
            for ticker, field_name, payload, status in fact_rows
        }
        for company in _PILOT:
            ticker = company["ticker"]
            exposure = self.loader._exposure(ticker, exposure_run_id)
            if exposure is None:
                comparisons.append({
                    "ticker": ticker,
                    "error": "MISSING_APPROVED_EXPOSURE_SNAPSHOT",
                })
                continue
            sector = self.loader._sector(exposure.sector, sector_run_id)
            if sector is None:
                comparisons.append({
                    "ticker": ticker,
                    "error": "MISSING_SECTOR_STATE",
                })
                continue
            channels = CompanyChannelTransport().from_sector_candidates(
                self.loader._sector_candidates(exposure.sector, sector_run_id)
            )
            outputs = {}
            for item in exposure.field_evidence:
                status = fact_status.get((
                    ticker, item.field_name, item.evidence_id
                ))
                if status and status not in {
                    "HUMAN_APPROVED", "DELEGATED_AI_APPROVED"
                }:
                    unapproved_inputs.append({
                        "ticker": ticker, "field_name": item.field_name,
                        "review_status": status,
                    })
            for policy, run_id in (
                ("THREE_COMPONENTS", "company_4c5b_three_components"),
                ("MATERIALITY_COVERAGE", "company_4c5b_materiality_coverage"),
            ):
                candidate = CompanyImpactEngine(run_id).evaluate(
                    sector, exposure, None, as_of_timestamp,
                    factor_channels=channels,
                    decision_policy=policy,
                )
                inserted += self.store.save_company_impact_candidate(
                    candidate.model_dump(mode="json")
                )
                written += 1
                outputs[policy] = candidate.model_dump(mode="json")
                if candidate.factor_contributions:
                    calculable_tickers.add(ticker)
                if (
                    sector.status == "SECTOR_STATE_NO_ACTIVE_SIGNAL"
                    and candidate.status == "NO_ACTION"
                    and not candidate.factor_contributions
                ):
                    no_active_signal_pass = True
            comparisons.append({
                "ticker": ticker,
                "sector_state": sector.status,
                "approved_snapshot_id": exposure.exposure_id,
                "approved_fields": sorted({
                    item.field_name for item in exposure.field_evidence
                    if item.source_type.startswith((
                        "CVM_", "RULE_DERIVED_POST_HEDGE"
                    ))
                }),
                "policies": outputs,
            })

        return {
            "selection_run_id": selection_run_id,
            "sector_run_id": sector_run_id,
            "exposure_run_id": exposure_run_id,
            "as_of_timestamp": as_of_timestamp.isoformat(),
            "companies_requested": len(_PILOT),
            "snapshots_built": build["snapshots_built"],
            "candidate_rows_written": written,
            "candidate_rows_inserted": inserted,
            "companies_with_calculable_contributions": len(calculable_tickers),
            "unapproved_fact_inputs_used": len(unapproved_inputs),
            "unapproved_input_details": unapproved_inputs,
            "no_active_signal_no_action_pass": no_active_signal_pass,
            "comparisons": comparisons,
            "sprint_4c_frozen": True,
            "valuation_enabled": False,
            "buy_enabled": False,
            "orders_enabled": False,
        }
