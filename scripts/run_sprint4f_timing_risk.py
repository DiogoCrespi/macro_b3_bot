"""
Sprint 4F: Timing, Risk & Invalidation Synthesis

Orchestrates the evaluation of timing classifications (MONITOR, WAIT_FOR_CONFIRMATION, AVOID),
risk classifications (LOW_RISK to UNACCEPTABLE_RISK), event freshness, pricing risk,
thesis invalidators, and review triggers based on 4E.3 ResearchDecisionSnapshot records.

Mandatory --as-of parameter is required.
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
    print("=== Sprint 4F: Timing, Risk & Invalidation Synthesis ===")
    as_of = parse_as_of_arg()
    print(f"Assessment Cutoff (as_of_timestamp): {as_of.isoformat()}")

    settings = Settings()
    db_path = settings.data_dir / "macro_b3_bot.duckdb"
    store = DatabaseStore(db_path)

    audits_dir = Path("data/audits")
    audit_4e3 = load_json_file(audits_dir / "research_4e3_decisions.json")

    synthesizer = ResearchTimingRiskSynthesizer()
    target_tickers = ["MGLU3", "SUZB3", "KLBN11", "RAIL3", "SLCE3"]

    decisions_map: dict[str, ResearchDecisionSnapshot] = {}
    
    # First check DuckDB
    stored_decisions = store.get_research_decision_snapshots()
    for d in stored_decisions:
        decisions_map[d["ticker"]] = ResearchDecisionSnapshot(**d)

    # Fallback to 4e3 audit file if DuckDB has missing ticker
    for dec_dict in audit_4e3.get("decisions", []):
        t = dec_dict.get("ticker")
        if t and t not in decisions_map:
            decisions_map[t] = ResearchDecisionSnapshot(**dec_dict)

    timing_snapshots = []

    for ticker in target_tickers:
        print(f"\nProcessing timing & risk for ticker: {ticker}...")
        decision_snapshot = decisions_map.get(ticker)

        if not decision_snapshot:
            # If decision snapshot is absent, create a safe fallback NO_ACTION snapshot
            decision_snapshot = ResearchDecisionSnapshot(
                decision_id=f"fallback_dec_{ticker}",
                ticker=ticker,
                as_of_timestamp=as_of.isoformat(),
                decision="NO_ACTION",
                critical_blockers=["BLOCKED_MISSING_UPSTREAM_INPUT"],
                execution_mode="BLOCKED_MISSING_UPSTREAM_INPUT",
            )

        timing_snapshot = synthesizer.synthesize(
            decision_snapshot=decision_snapshot,
            as_of_timestamp=as_of,
            input_ids={"research_decision_id": decision_snapshot.decision_id},
        )

        timing_snapshots.append(timing_snapshot)

        # Persist to DuckDB idempotently
        store.save_research_timing_risk_snapshot(timing_snapshot.model_dump(mode="json"))

    # Save audit manifest file
    out_dir = Path("data/audits")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "research_4f_timing_risk.json"

    manifest_payload = {
        "sprint": "4F",
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
    print(f"{'Ticker':<10} | {'Timing':<22} | {'Risk Classification':<20} | {'Confidence':<10} | {'Risk Flags'}")
    print("-" * 105)
    for s in timing_snapshots:
        flags = ", ".join(s.risk_flags[:3]) if s.risk_flags else "NONE"
        print(f"{s.ticker:<10} | {s.timing_classification:<22} | {s.risk_classification:<20} | {s.confidence:<10.4f} | {flags}")

    store.close()


if __name__ == "__main__":
    main()
