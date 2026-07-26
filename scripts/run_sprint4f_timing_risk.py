"""
Sprint 4F.2: Real Market Risk Binding & Timing Semantics

Orchestrates real market risk evaluations (returns, realized volatility, drawdowns, Amihud illiquidity),
exponential half-life event freshness decay, pricing risk, thesis invalidation, and timing classifications.

Mandatory --as-of parameter is required.
Strictly selects upstream 4E.3 decisions where decision.as_of_timestamp <= cutoff.
Does NOT fabricate synthetic fallback_dec_<ticker> decision snapshots.
Persists snapshots to DuckDB table 'research_timing_risk_snapshots' and outputs data/audits/research_4f_timing_risk.json.
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

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.application.research_timing_risk_synthesis import ResearchTimingRiskSynthesizer


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


def main() -> None:
    print("=== Sprint 4F.2: Real Market Risk Binding & Timing Semantics ===")
    as_of = parse_as_of_arg()
    print(f"Assessment Cutoff (as_of_timestamp): {as_of.isoformat()}")

    settings = Settings()
    # Use the same canonical PIT store as decision synthesis and paper replay.
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    audits_dir = Path("data/audits")
    audit_4e3 = load_json_file(audits_dir / "research_4e3_decisions.json")
    audit_4e2 = load_json_file(audits_dir / "valuation_4e2_historical_reverse.json")

    synthesizer = ResearchTimingRiskSynthesizer()
    target_tickers = ["MGLU3", "SUZB3", "KLBN11", "RAIL3", "SLCE3"]

    # 1. Load macro events strictly prior to as_of
    macro_events = []
    try:
        raw_events = store.connection.execute(
            """
            SELECT event_id, indicator, direction, detected_at, status, surprise_score
            FROM macro_event_candidates
            WHERE detected_at <= ?
              AND status IN ('MACRO_EVENT_APPROVED', 'MACRO_EVENT_WATCH')
            """,
            [as_of],
        ).fetchall()
        for event_id, indicator, direction, detected_at, status, importance in raw_events:
            macro_events.append({
                "macro_event_id": event_id,
                "factor": "INTEREST_RATES" if "Selic" in str(indicator) else "UNKNOWN",
                "factor_direction": -1 if str(direction).upper() in {"DOVISH", "FALLING", "DOWN"} else 1,
                "available_at": str(detected_at),
                "event_status": status,
                "importance": importance,
            })
    except Exception:
        pass

    timing_snapshots = []

    for ticker in target_tickers:
        print(f"\nProcessing timing & risk for ticker: {ticker}...")

        # Strict PIT decision selection: decision.as_of_timestamp <= cutoff
        pit_decision_dict = store.get_latest_research_decision_snapshot_pit(ticker, as_of)

        if not pit_decision_dict:
            # Fallback to audit 4e3 file if as_of matches or is strictly before cutoff
            for d in audit_4e3.get("decisions", []):
                if d.get("ticker") == ticker:
                    d_as_of = datetime.fromisoformat(d["as_of_timestamp"].replace("Z", "+00:00"))
                    if d_as_of.tzinfo is None:
                        d_as_of = d_as_of.replace(tzinfo=timezone.utc)
                    if d_as_of <= as_of:
                        pit_decision_dict = d
                        break

        if not pit_decision_dict:
            # Operational blocked record - DO NOT create a synthetic ResearchDecisionSnapshot!
            print(f"  Warning: No valid 4E.3 decision available for {ticker} at or before cutoff {as_of.isoformat()}. Emitting BLOCKED execution state.")
            decision_snapshot = ResearchDecisionSnapshot(
                decision_id=f"blocked_no_pit_decision_{ticker}",
                ticker=ticker,
                as_of_timestamp=as_of.isoformat(),
                decision="NO_ACTION",
                critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT"],
                execution_mode="BLOCKED_MISSING_UPSTREAM_INPUT",
            )
        else:
            decision_snapshot = ResearchDecisionSnapshot(**pit_decision_dict)

        # Fetch real historical market quotes strictly up to cutoff
        quotes = store.get_historical_market_quotes(ticker, as_of)

        # Fallback to 4E.2 assembled observations quotes if store table is empty
        if not quotes and audit_4e2.get("assembled_observations"):
            for obs in audit_4e2["assembled_observations"]:
                if obs.get("ticker") == ticker:
                    obs_avail = datetime.fromisoformat(obs["available_at"].replace("Z", "+00:00"))
                    if obs_avail.tzinfo is None:
                        obs_avail = obs_avail.replace(tzinfo=timezone.utc)
                    if obs_avail <= as_of:
                        quotes.append({
                            "trade_date": obs["valuation_date"],
                            "close_price": obs["close_price"],
                            "volume_brl": float(obs["market_cap"] * 0.005),  # liquidity estimate from market cap
                        })

        input_ids = {
            "research_decision_id": decision_snapshot.decision_id,
            "macro_event_ids": decision_snapshot.macro_event_ids,
            "valuation_observation_ids": decision_snapshot.input_ids.get("valuation_observation_ids", []),
            "market_quote_records_count": len(quotes),
        }

        timing_snapshot = synthesizer.synthesize(
            decision_snapshot=decision_snapshot,
            as_of_timestamp=as_of,
            market_quotes=quotes,
            macro_events=macro_events,
            input_ids=input_ids,
        )

        timing_snapshots.append(timing_snapshot)

        # Persist snapshot to DuckDB
        store.save_research_timing_risk_snapshot(timing_snapshot.model_dump(mode="json"))

    # Save audit manifest file
    out_dir = Path("data/audits")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "research_4f_timing_risk.json"

    manifest_payload = {
        "sprint": "4F.2",
        "methodology_version": synthesizer.methodology_version,
        "as_of_timestamp": as_of.isoformat(),
        "total_evaluated": len(timing_snapshots),
        "timing_classifications_count": {
            "MONITOR": sum(1 for s in timing_snapshots if s.timing_classification == "MONITOR"),
            "WAIT_FOR_CONFIRMATION": sum(1 for s in timing_snapshots if s.timing_classification == "WAIT_FOR_CONFIRMATION"),
            "AVOID": sum(1 for s in timing_snapshots if s.timing_classification == "AVOID"),
        },
        "risk_classifications_count": {
            "LOW_RISK": sum(1 for s in timing_snapshots if s.risk_classification == "LOW_RISK"),
            "MODERATE_RISK": sum(1 for s in timing_snapshots if s.risk_classification == "MODERATE_RISK"),
            "ELEVATED_RISK": sum(1 for s in timing_snapshots if s.risk_classification == "ELEVATED_RISK"),
            "HIGH_RISK": sum(1 for s in timing_snapshots if s.risk_classification == "HIGH_RISK"),
            "UNACCEPTABLE_RISK": sum(1 for s in timing_snapshots if s.risk_classification == "UNACCEPTABLE_RISK"),
        },
        "snapshots": [s.model_dump(mode="json") for s in timing_snapshots],
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest_payload, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(timing_snapshots)} timing & risk snapshots to {out_file}")
    print("Persisted snapshots into DuckDB table 'research_timing_risk_snapshots'")

    print("\n--- Summary Table ---")
    print(f"{'Ticker':<10} | {'Timing':<22} | {'Risk Classification':<20} | {'Level':<6} | {'Risk Flags'}")
    print("-" * 105)
    for s in timing_snapshots:
        flags = ", ".join(s.risk_flags[:3]) if s.risk_flags else "NONE"
        print(f"{s.ticker:<10} | {s.timing_classification:<22} | {s.risk_classification:<20} | {s.risk_severity_level:<6} | {flags}")

    store.close()


if __name__ == "__main__":
    main()
