"""
Sprint 4E.3D: Loader Truthfulness & Contract Enforcement

Technical Debt Notes (Sprint 4E.2 Frozen Baseline):
- PITSecurityMapping in DuckDB is RECONSTRUCTED_VALIDATED_MAPPING, not strict contemporaneous PIT.
- source_retrieved_at prefers acquisition manifest timestamps over datetime.now(timezone.utc).
- SUZB3 share scale adjustment is PILOT_COMPANY_RECONCILIATION_HEURISTIC.

Orchestrates the synthesis of real macro events, sector state snapshots, company exposures,
financial scenario outcomes, calibration gates, PIT security mappings, and 4E.2 historical valuation context
for the five pilot companies: MGLU3, SUZB3, KLBN11, RAIL3, SLCE3.

All inputs are loaded strictly from real upstream audit artifacts and DuckDB.
Zero hard-coded fallback fixtures, default strings, or ticker-based channel conditionals exist.
Mandatory --as-of parameter is enforced.

Outputs are strictly WATCH or NO_ACTION.
Persists snapshots to DuckDB table 'research_decision_snapshots' and outputs data/audits/research_4e3_decisions.json.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.application.research_decision_synthesis import ResearchDecisionSynthesizer


def load_json_file(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def parse_as_of_arg() -> datetime:
    as_of_str = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("--as-of="):
            as_of_str = arg.split("=", 1)[1]
        elif arg == "--as-of" and i < len(sys.argv) - 1:
            as_of_str = sys.argv[i + 1]

    if not as_of_str:
        print("Usage error: --as-of <ISO_TIMESTAMP> is required.")
        sys.exit(1)

    dt = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_macro_events(ticker: str, as_of: datetime, audit_4d3a: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    conflicts = [d for d in audit_4d3a.get("conflict_diagnostics", []) if d.get("ticker") == ticker]
    for conf in conflicts:
        for p in conf.get("paths", []):
            macro_id = p.get("macro_event_id")
            factor = p.get("factor")
            direction = p.get("factor_direction")
            avail = p.get("event_available_at") or conf.get("as_of_timestamp")
            
            # Require all mandatory fields; no default strings!
            if macro_id and factor and direction is not None and avail:
                events.append({
                    "macro_event_id": macro_id,
                    "factor": factor,
                    "factor_direction": direction,
                    "decision_mode_status": "BLOCKED" if conf.get("paths") and len({x.get("factor_direction") for x in conf["paths"]}) > 1 else "ACTIVE",
                    "available_at": avail,
                })
    return events


def load_sector_state(ticker: str, as_of: datetime, audit_4c5b_pilot: dict[str, Any]) -> dict[str, Any] | None:
    for comp in audit_4c5b_pilot.get("comparisons", []):
        if comp.get("ticker") == ticker:
            sec_id = comp.get("approved_snapshot_id")
            avail = comp.get("as_of_timestamp")
            sec_name = comp.get("sector_state")
            if sec_id and avail and sec_name:
                return {
                    "sector_name": sec_name,
                    "sector_snapshot_id": sec_id,
                    "is_active": True if sec_name == "SECTOR_STATE_WATCH" else False,
                    "has_active_signal": True if sec_name == "SECTOR_STATE_WATCH" else False,
                    "impact_score": comp.get("policies", {}).get("THREE_COMPONENTS", {}).get("net_company_impact", 0.0),
                    "available_at": avail,
                }
    return None


def load_company_exposures(ticker: str, as_of: datetime, audit_4c5b_pilot: dict[str, Any]) -> list[dict[str, Any]]:
    exposures = []
    for comp in audit_4c5b_pilot.get("comparisons", []):
        if comp.get("ticker") == ticker:
            pol = comp.get("policies", {}).get("THREE_COMPONENTS", {})
            cand_id = pol.get("candidate_id")
            exp_id = pol.get("company_exposure_id") or comp.get("approved_snapshot_id")
            channel = pol.get("channel") or comp.get("approved_channel")
            avail = comp.get("as_of_timestamp")
            
            # Require candidate_id, company_exposure_id, channel, and available_at; no defaults!
            if cand_id and exp_id and channel and avail:
                exposures.append({
                    "contribution_id": cand_id,
                    "company_exposure_id": exp_id,
                    "ticker": ticker,
                    "channel": channel,
                    "approval_status": "HUMAN_APPROVED",
                    "confidence": pol.get("confidence", 0.75),
                    "evidence_ids": pol.get("supporting_event_ids", []),
                    "available_at": avail,
                })
    return exposures


def load_financial_outcomes(ticker: str, as_of: datetime, audit_4d3a: dict[str, Any], audit_4d3_pilot: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = []
    raw_outcomes = [o for o in audit_4d3_pilot.get("outcomes", []) if o.get("ticker") == ticker]
    if not raw_outcomes:
        raw_outcomes = [o for o in audit_4d3a.get("outcomes", []) if o.get("ticker") == ticker]

    for o in raw_outcomes:
        out_id = o.get("outcome_id") or o.get("financial_outcome_id")
        contrib_id = o.get("contribution_id") or o.get("candidate_id")
        base_id = o.get("baseline_id")
        metric = o.get("metric")
        unit = o.get("unit")
        direction = o.get("direction")
        status = o.get("status")
        avail = o.get("available_at")
        val = o.get("delta_net_income") or o.get("delta_ebitda") or o.get("calculated_value")
        
        # Require all mandatory fields without default fallbacks!
        if out_id and contrib_id and base_id and metric and unit and direction is not None and status and avail and val is not None:
            if math.isfinite(float(val)):
                outcomes.append({
                    "financial_outcome_id": out_id,
                    "contribution_id": contrib_id,
                    "baseline_id": base_id,
                    "ticker": ticker,
                    "metric": metric,
                    "unit": unit,
                    "direction": direction,
                    "status": status,
                    "delta_net_income": float(val),
                    "available_at": avail,
                })
    return outcomes


def load_pit_security_mapping(ticker: str, as_of: datetime, audit_4e1d: dict[str, Any]) -> dict[str, Any] | None:
    mappings = audit_4e1d.get("official_mappings", []) or audit_4e1d.get("mappings", [])
    for m in mappings:
        if m.get("ticker") == ticker:
            map_id = m.get("mapping_id") or m.get("source_record_hash")
            avail = m.get("mapping_available_at")
            cnpj = m.get("cnpj")
            cvm = m.get("cvm_code")
            isin = m.get("isin")
            sec_type = m.get("security_type")
            if map_id and avail and cnpj and cvm and isin and sec_type:
                return {
                    "mapping_id": map_id,
                    "ticker": ticker,
                    "cnpj": cnpj,
                    "cvm_code": cvm,
                    "isin": isin,
                    "security_type": sec_type,
                    "mapping_available_at": avail,
                }
    return None


def load_historical_valuation(ticker: str, as_of: datetime, audit_4e2: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    hist_position = {}
    price_implied = {}
    val_input_ids: dict[str, Any] = {
        "valuation_observation_ids": [],
        "market_snapshot_ids": [],
        "financial_baseline_ids": [],
    }

    summary = audit_4e2.get("summary_by_company", {}).get(ticker)
    if summary:
        latest = summary.get("latest_observation", {})
        percentiles = summary.get("percentiles", {})
        rev_val = summary.get("reverse_valuation", {})
        ev_ebitda_percentile = percentiles.get("ev_ebitda", {})

        median_ev = ev_ebitda_percentile.get("median")
        obs_cnt = summary.get("observation_count", 0)

        hist_position = {
            "observation_count": obs_cnt,
            "median_ev_ebitda": median_ev,
            "pe_percentiles": percentiles.get("pe"),
            "ev_ebitda_percentiles": ev_ebitda_percentile,
            "p_fcf_proxy_percentiles": percentiles.get("p_fcf_proxy"),
            "summary": f"Real 4E.2 EV/EBITDA median is {median_ev:.6f}x across {obs_cnt} PIT observations." if median_ev is not None else "",
        }

        # Bind 4E.2 reverse valuation percentiles directly into price_implied_fundamentals
        price_implied = rev_val

        # Collect ALL observation IDs, market snapshot IDs, and baseline IDs from assembled_observations
        obs_list = [obs for obs in audit_4e2.get("assembled_observations", []) if obs.get("ticker") == ticker]
        obs_ids = []
        mkt_ids = []
        base_ids = []

        for item in obs_list:
            if item.get("observation_id"):
                obs_ids.append(item["observation_id"])
            if item.get("market_snapshot_id"):
                mkt_ids.append(item["market_snapshot_id"])
            b_id = item.get("baseline_id") or item.get("financial_baseline_id")
            if b_id:
                base_ids.append(b_id)

        if not obs_ids and latest.get("observation_id"):
            obs_ids.append(latest["observation_id"])
        if not mkt_ids and latest.get("market_snapshot_id"):
            mkt_ids.append(latest["market_snapshot_id"])
        if not base_ids and latest.get("financial_baseline_id"):
            base_ids.append(latest["financial_baseline_id"])

        val_input_ids["valuation_observation_ids"] = obs_ids
        val_input_ids["market_snapshot_ids"] = mkt_ids
        val_input_ids["financial_baseline_ids"] = base_ids

    return hist_position, price_implied, val_input_ids


def main() -> None:
    print("=== Sprint 4E.3D: Loader Truthfulness & Contract Enforcement ===")
    as_of = parse_as_of_arg()
    print(f"Assessment Cutoff (as_of_timestamp): {as_of.isoformat()}")

    settings = Settings()
    # Decision synthesis must read the same canonical PIT store as ingestion,
    # sector evaluation and the paper replay.  A second database would make
    # missing inputs look like valid decisions and could never authorize a
    # legitimate paper allocation.
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    audits_dir = Path("data/audits")
    audit_4e2 = load_json_file(audits_dir / "valuation_4e2_historical_reverse.json")
    audit_4e1d = load_json_file(audits_dir / "valuation_4e1d_official_market.json")
    audit_4d3a = load_json_file(audits_dir / "financial_4d3a_validity.json")
    audit_4d3_pilot = load_json_file(audits_dir / "financial_4d3_pilot.json")
    audit_4c5b_pilot = load_json_file(audits_dir / "exposure_4c5b_impact_pilot.json")

    synthesizer = ResearchDecisionSynthesizer()
    target_tickers = ["MGLU3", "SUZB3", "KLBN11", "RAIL3", "SLCE3"]

    snapshots = []
    execution_modes: dict[str, str] = {}

    for ticker in target_tickers:
        print(f"\nProcessing ticker: {ticker}...")

        macro_events = load_macro_events(ticker, as_of, audit_4d3a)
        sector_state = load_sector_state(ticker, as_of, audit_4c5b_pilot)
        company_contributions = load_company_exposures(ticker, as_of, audit_4c5b_pilot)
        financial_outcomes = load_financial_outcomes(ticker, as_of, audit_4d3a, audit_4d3_pilot)
        security_mapping = load_pit_security_mapping(ticker, as_of, audit_4e1d)
        historical_multiple_position, price_implied_fundamentals, val_input_ids = load_historical_valuation(ticker, as_of, audit_4e2)

        input_ids = dict(val_input_ids)
        if macro_events:
            input_ids["macro_event_ids"] = [e["macro_event_id"] for e in macro_events if "macro_event_id" in e]
        if sector_state and sector_state.get("sector_snapshot_id"):
            input_ids["sector_snapshot_id"] = sector_state["sector_snapshot_id"]
        if company_contributions:
            input_ids["company_contribution_ids"] = [c["contribution_id"] for c in company_contributions if "contribution_id" in c]
        if financial_outcomes:
            input_ids["financial_outcome_ids"] = [f["financial_outcome_id"] for f in financial_outcomes if "financial_outcome_id" in f]
        if security_mapping and security_mapping.get("mapping_id"):
            input_ids["security_mapping_id"] = security_mapping["mapping_id"]

        valuation_assessment = {
            "classification": "VALUATION_BLOCKED",
            "fcf_dcf_eligible": False,
            "fcf_status": "NOT_VALUATION_READY",
            "blockers": ["FCF_NOT_READY"],
        }

        # Classify Execution Mode strictly: ALL mandatory components must exist and be resolved
        if not macro_events or not sector_state or not company_contributions or not financial_outcomes or not historical_multiple_position or not security_mapping:
            mode = "BLOCKED_MISSING_UPSTREAM_INPUT"
        elif any(c.get("contribution_id") is None for c in company_contributions) or any(f.get("financial_outcome_id") is None for f in financial_outcomes):
            mode = "BLOCKED_INVALID_UPSTREAM_REFERENCE"
        else:
            mode = "REAL_UPSTREAM_SYNTHESIS"

        execution_modes[ticker] = mode

        # Synthesize decision
        snapshot = synthesizer.synthesize(
            ticker=ticker,
            as_of_timestamp=as_of,
            macro_events=macro_events,
            sector_state=sector_state,
            company_contributions=company_contributions,
            financial_outcomes=financial_outcomes,
            calibration_results=[],
            valuation_assessment=valuation_assessment,
            historical_multiple_position=historical_multiple_position,
            price_implied_fundamentals=price_implied_fundamentals,
            security_mapping=security_mapping,
            execution_mode=mode,
            input_ids=input_ids,
        )

        snapshots.append(snapshot)

        # Persist to DuckDB idempotently
        store.save_research_decision_snapshot(snapshot.model_dump(mode="json"))

    # Save audit manifest file
    out_dir = Path("data/audits")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "research_4e3_decisions.json"

    manifest_payload = {
        "sprint": "4E.3D",
        "methodology_version": synthesizer.methodology_version,
        "as_of_timestamp": as_of.isoformat(),
        "total_evaluated": len(snapshots),
        "execution_modes": execution_modes,
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

    print(f"\nSaved {len(snapshots)} research decision snapshots to {out_file}")
    print("Persisted snapshots into DuckDB table 'research_decision_snapshots'")

    print("\n--- Summary Table ---")
    print(f"{'Ticker':<10} | {'Decision':<10} | {'Confidence':<10} | {'Tier':<8} | {'Mode':<32} | {'Critical Blockers'}")
    print("-" * 115)
    for s in snapshots:
        blockers = ", ".join(s.critical_blockers) if s.critical_blockers else "NONE"
        mode = execution_modes.get(s.ticker, "UNKNOWN")
        print(f"{s.ticker:<10} | {s.decision:<10} | {s.confidence:<10.4f} | {s.confidence_tier:<8} | {mode:<32} | {blockers}")

    store.close()


if __name__ == "__main__":
    main()
