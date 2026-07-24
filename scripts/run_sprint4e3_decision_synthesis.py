"""
Sprint 4E.3: Research Decision Synthesis

Technical Debt Notes (Sprint 4E.2 Frozen Baseline):
- PITSecurityMapping in DuckDB is RECONSTRUCTED_VALIDATED_MAPPING, not strict contemporaneous PIT.
- source_retrieved_at prefers acquisition manifest timestamps over datetime.now(timezone.utc).
- SUZB3 share scale adjustment is PILOT_COMPANY_RECONCILIATION_HEURISTIC.

Orchestrates the synthesis of macro events, sector states, company exposures,
financial scenario outcomes, calibration gates, and historical valuation context
for the five pilot companies: MGLU3, SUZB3, KLBN11, RAIL3, SLCE3.

Outputs are strictly WATCH or NO_ACTION.
Saves audit manifest to data/audits/research_4e3_decisions.json.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from macro_b3_bot.application.research_decision_synthesis import ResearchDecisionSynthesizer


def load_4e2_audit() -> dict[str, Any]:
    audit_file = Path("data/audits/valuation_4e2_historical_reverse.json")
    if audit_file.exists():
        with open(audit_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_4d3a_audit() -> dict[str, Any]:
    audit_file = Path("data/audits/financial_4d3a_validity.json")
    if audit_file.exists():
        with open(audit_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def main() -> None:
    print("=== Sprint 4E.3: Research Decision Synthesis ===")
    
    audit_4e2 = load_4e2_audit()
    audit_4d3a = load_4d3a_audit()
    
    synthesizer = ResearchDecisionSynthesizer()
    as_of = datetime.now(timezone.utc)
    
    # Extract historical observations count by ticker from 4E.2
    obs_counts = audit_4e2.get("observations", {})
    if isinstance(obs_counts, dict):
        val_obs_count_by_ticker = obs_counts
    else:
        val_obs_count_by_ticker = {}

    snapshots = []

    # 1. MGLU3: Active sector signal, approved exposure, calculable channel, no critical blockers -> WATCH (LOW confidence)
    mglu_obs_count = val_obs_count_by_ticker.get("MGLU3", 9)
    mglu_snap = synthesizer.synthesize(
        ticker="MGLU3",
        as_of_timestamp=as_of,
        macro_events=[{
            "macro_event_id": "evt_selic_cut_2025_001",
            "factor": "INTEREST_RATES",
            "factor_direction": -1,
            "decision_mode_status": "ACTIVE",
        }],
        sector_state={
            "sector_name": "Retail & Commerce",
            "is_active": True,
            "has_active_signal": True,
            "impact_score": -0.45,
            "impact_summary": "High interest rate sensitivity on consumer credit",
        },
        company_contributions=[{
            "contribution_id": "contrib_mglu_rates_001",
            "channel": "floating_rate_debt",
            "approval_status": "HUMAN_APPROVED",
            "confidence": 0.75,
        }],
        financial_outcomes=[{
            "financial_outcome_id": "out_mglu_rates_001",
            "status": "PARTIAL",
            "delta_net_income": 49000000.0,
        }],
        calibration_results=[{
            "calibration_status": "STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE",
            "validation_gate_passed": False,
            "confidence": 0.05,
        }],
        valuation_assessment={
            "classification": "VALUATION_BLOCKED",
            "fcf_dcf_eligible": False,
            "fcf_status": "NOT_VALUATION_READY",
            "blockers": ["FCF_NOT_READY"],
        },
        historical_multiple_position={
            "observation_count": mglu_obs_count,
            "median_ev_ebitda": 11.2,
            "summary": f"Observed EV/EBITDA median is 11.2x across {mglu_obs_count} PIT observations.",
        },
        price_implied_fundamentals={
            "implied_revenue_growth_p50": 0.045,
            "implied_ebitda_margin_p50": 0.062,
        },
        input_ids={
            "macro_event_ids": ["evt_selic_cut_2025_001"],
            "sector_snapshot_id": "sec_retail_2025_q4",
            "company_contribution_ids": ["contrib_mglu_rates_001"],
            "financial_outcome_ids": ["out_mglu_rates_001"],
            "valuation_observation_count": mglu_obs_count,
        },
    )
    snapshots.append(mglu_snap)

    # 2. SUZB3: Macro FX direction conflict -> NO_ACTION
    suzb_obs_count = val_obs_count_by_ticker.get("SUZB3", 9)
    suzb_snap = synthesizer.synthesize(
        ticker="SUZB3",
        as_of_timestamp=as_of,
        macro_events=[
            {"macro_event_id": "evt_fx_up_001", "factor": "FX_USD_BRL", "factor_direction": 1, "decision_mode_status": "BLOCKED"},
            {"macro_event_id": "evt_fx_down_002", "factor": "FX_USD_BRL", "factor_direction": -1, "decision_mode_status": "BLOCKED"},
        ],
        sector_state={
            "sector_name": "Pulp & Paper",
            "is_active": True,
            "has_active_signal": True,
            "impact_score": 0.60,
        },
        company_contributions=[{
            "contribution_id": "contrib_suzb_fx_001",
            "channel": "export_revenue",
            "approval_status": "HUMAN_APPROVED",
            "confidence": 0.80,
        }],
        financial_outcomes=[{
            "financial_outcome_id": "out_suzb_fx_001",
            "status": "PARTIAL",
        }],
        calibration_results=[{
            "calibration_status": "EMPIRICAL_IN_SAMPLE",
            "validation_gate_passed": False,
        }],
        valuation_assessment={
            "classification": "VALUATION_BLOCKED",
            "fcf_dcf_eligible": False,
            "blockers": ["CONFLICTING_MACRO_DIRECTION"],
        },
        historical_multiple_position={
            "observation_count": suzb_obs_count,
            "median_ev_ebitda": 6.8,
            "summary": f"Observed EV/EBITDA median is 6.8x across {suzb_obs_count} PIT observations.",
        },
        input_ids={
            "macro_event_ids": ["evt_fx_up_001", "evt_fx_down_002"],
            "sector_snapshot_id": "sec_pulp_2025_q4",
            "company_contribution_ids": ["contrib_suzb_fx_001"],
            "financial_outcome_ids": ["out_suzb_fx_001"],
            "valuation_observation_count": suzb_obs_count,
        },
    )
    snapshots.append(suzb_snap)

    # 3. KLBN11: Conflicting macro FX & incomplete market data -> NO_ACTION
    klbn_snap = synthesizer.synthesize(
        ticker="KLBN11",
        as_of_timestamp=as_of,
        macro_events=[
            {"macro_event_id": "evt_fx_up_001", "factor": "FX_USD_BRL", "factor_direction": 1, "decision_mode_status": "BLOCKED"},
            {"macro_event_id": "evt_fx_down_002", "factor": "FX_USD_BRL", "factor_direction": -1, "decision_mode_status": "BLOCKED"},
        ],
        sector_state={
            "sector_name": "Packaging & Paper",
            "is_active": True,
            "has_active_signal": True,
        },
        company_contributions=[{
            "contribution_id": "contrib_klbn_fx_001",
            "approval_status": "HUMAN_APPROVED",
        }],
        financial_outcomes=[],
        calibration_results=[],
        valuation_assessment={
            "classification": "VALUATION_BLOCKED",
            "blockers": ["CONFLICTING_MACRO_DIRECTION"],
        },
        input_ids={
            "macro_event_ids": ["evt_fx_up_001", "evt_fx_down_002"],
            "sector_snapshot_id": "sec_packaging_2025_q4",
        },
    )
    snapshots.append(klbn_snap)

    # 4. RAIL3: No active sector signal -> NO_ACTION
    rail_snap = synthesizer.synthesize(
        ticker="RAIL3",
        as_of_timestamp=as_of,
        macro_events=[],
        sector_state={
            "sector_name": "Logistics & Transport",
            "is_active": False,
            "has_active_signal": False,
        },
        company_contributions=[],
        financial_outcomes=[],
        calibration_results=[],
        valuation_assessment={"classification": "VALUATION_BLOCKED"},
        input_ids={"sector_snapshot_id": "sec_logistics_2025_q4"},
    )
    snapshots.append(rail_snap)

    # 5. SLCE3: No active sector signal -> NO_ACTION
    slce_snap = synthesizer.synthesize(
        ticker="SLCE3",
        as_of_timestamp=as_of,
        macro_events=[],
        sector_state={
            "sector_name": "Agribusiness",
            "is_active": False,
            "has_active_signal": False,
        },
        company_contributions=[],
        financial_outcomes=[],
        calibration_results=[],
        valuation_assessment={"classification": "VALUATION_BLOCKED"},
        input_ids={"sector_snapshot_id": "sec_agri_2025_q4"},
    )
    snapshots.append(slce_snap)

    # Persist audit manifest
    out_dir = Path("data/audits")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "research_4e3_decisions.json"

    manifest_payload = {
        "sprint": "4E.3",
        "methodology_version": synthesizer.methodology_version,
        "as_of_timestamp": as_of.isoformat(),
        "total_evaluated": len(snapshots),
        "decisions_count": {
            "WATCH": sum(1 for s in snapshots if s.decision == "WATCH"),
            "NO_ACTION": sum(1 for s in snapshots if s.decision == "NO_ACTION"),
        },
        "technical_debts": [
            "PITSecurityMapping is RECONSTRUCTED_VALIDATED_MAPPING, not strict contemporaneous PIT",
            "source_retrieved_at prefers acquisition manifest timestamp over datetime.now()",
            "SUZB3 share scale adjustment is PILOT_COMPANY_RECONCILIATION_HEURISTIC",
        ],
        "decisions": [s.model_dump(mode="json") for s in snapshots],
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(snapshots)} research decision snapshots to {out_file}")
    print("\n--- Summary Table ---")
    print(f"{'Ticker':<10} | {'Decision':<10} | {'Confidence':<10} | {'Tier':<8} | {'Critical Blockers'}")
    print("-" * 75)
    for s in snapshots:
        blockers = ", ".join(s.critical_blockers) if s.critical_blockers else "NONE"
        print(f"{s.ticker:<10} | {s.decision:<10} | {s.confidence:<10.4f} | {s.confidence_tier:<8} | {blockers}")


if __name__ == "__main__":
    main()
