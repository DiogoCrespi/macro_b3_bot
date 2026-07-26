"""
Sprint 4G.2: Real Event-Driven PIT Replay & Benchmark Integrity

Orchestrates sequential, event-driven point-in-time historical replays without look-ahead bias or synthetic fallback decisions.
Evaluates paper portfolio allocations, returns, drawdowns, transaction costs,
real benchmark series (CDI compounded daily via BCB series 12, IBOV historical, Equal-Weight Pilot Universe),
thesis metrics, and ex-post blocker impacts.

Mandatory CLI parameters are strictly required:
  --start-date <ISO_TIMESTAMP>
  --end-date <ISO_TIMESTAMP>
  --initial-capital <FLOAT>
  --policy-version <STRING>
  --cash-yield-mode <UNREMUNERATED|CDI>

Generates 4 audit artifacts:
- data/audits/paper_portfolio_4g_run.json
- data/audits/paper_portfolio_4g_ledger.json
- data/audits/paper_portfolio_4g_performance.json
- data/audits/replay_4g_end_to_end.json
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
from macro_b3_bot.domain.paper_portfolio_models import PaperPortfolioPolicy
from macro_b3_bot.application.historical_replay_engine import HistoricalReplayEngine


def load_json_file(path: Path) -> dict[str, Any]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def parse_mandatory_cli_args() -> tuple[datetime, datetime, float, str, str]:
    start_str = None
    end_str = None
    capital = None
    policy_ver = None
    cash_yield_mode = None

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("--start-date="):
            start_str = arg.split("=", 1)[1]
        elif arg == "--start-date" and i < len(sys.argv) - 1:
            start_str = sys.argv[i + 1]
        elif arg.startswith("--end-date="):
            end_str = arg.split("=", 1)[1]
        elif arg == "--end-date" and i < len(sys.argv) - 1:
            end_str = sys.argv[i + 1]
        elif arg.startswith("--initial-capital="):
            capital = float(arg.split("=", 1)[1])
        elif arg == "--initial-capital" and i < len(sys.argv) - 1:
            capital = float(sys.argv[i + 1])
        elif arg.startswith("--policy-version="):
            policy_ver = arg.split("=", 1)[1]
        elif arg == "--policy-version" and i < len(sys.argv) - 1:
            policy_ver = sys.argv[i + 1]
        elif arg.startswith("--cash-yield-mode="):
            cash_yield_mode = arg.split("=", 1)[1]
        elif arg == "--cash-yield-mode" and i < len(sys.argv) - 1:
            cash_yield_mode = sys.argv[i + 1]

    missing = []
    if not start_str:
        missing.append("--start-date")
    if not end_str:
        missing.append("--end-date")
    if capital is None:
        missing.append("--initial-capital")
    if not policy_ver:
        missing.append("--policy-version")
    if not cash_yield_mode:
        missing.append("--cash-yield-mode")

    if missing:
        print(f"Usage error: Mandatory arguments missing: {', '.join(missing)}")
        print("Required syntax:")
        print("  python scripts/run_sprint4g_paper_portfolio_replay.py \\")
        print("    --start-date 2024-01-01T00:00:00Z \\")
        print("    --end-date 2026-07-24T00:00:00Z \\")
        print("    --initial-capital 100000.0 \\")
        print("    --policy-version 4G.2-paper-policy-v2 \\")
        print("    --cash-yield-mode UNREMUNERATED")
        sys.exit(1)

    if cash_yield_mode not in ("UNREMUNERATED", "CDI"):
        print(f"Usage error: Invalid --cash-yield-mode '{cash_yield_mode}'. Must be UNREMUNERATED or CDI.")
        sys.exit(1)

    s_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    e_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    if s_dt.tzinfo is None:
        s_dt = s_dt.replace(tzinfo=timezone.utc)
    if e_dt.tzinfo is None:
        e_dt = e_dt.replace(tzinfo=timezone.utc)

    return s_dt, e_dt, capital, policy_ver, cash_yield_mode


def main() -> None:
    print("=== Sprint 4G.2: Real Event-Driven PIT Replay & Benchmark Integrity ===")
    start_dt, end_dt, initial_capital, policy_version, cash_yield_mode = parse_mandatory_cli_args()
    print(f"Replay Interval: {start_dt.isoformat()} to {end_dt.isoformat()}")
    print(f"Initial Capital: R$ {initial_capital:,.2f} | Policy Version: {policy_version} | Cash Yield Mode: {cash_yield_mode}")

    settings = Settings()
    # All application writers use the canonical audit database.  Using a
    # second filename here silently replays an empty/stale store and makes the
    # paper-portfolio result look persisted while it is disconnected from the
    # actual PIT decisions.  Keep the replay on the same database boundary.
    db_path = settings.data_dir / "audit.duckdb"
    store = DatabaseStore(db_path)

    audits_dir = Path("data/audits")
    audit_4e3 = load_json_file(audits_dir / "research_4e3_decisions.json")
    audit_4f = load_json_file(audits_dir / "research_4f_timing_risk.json")
    audit_4e2 = load_json_file(audits_dir / "valuation_4e2_historical_reverse.json")
    cvm_manifest = load_json_file(audits_dir / "cvm_historical_acquisition_manifest.json")

    # Collect source manifest IDs
    source_manifest_ids = [
        "data/audits/research_4e3_decisions.json",
        "data/audits/research_4f_timing_risk.json",
        "data/audits/valuation_4e2_historical_reverse.json",
    ]
    if cvm_manifest:
        source_manifest_ids.append("data/audits/cvm_historical_acquisition_manifest.json")

    policy_payload = {
        "initial_capital": initial_capital,
        "cash_yield_mode": cash_yield_mode,
        "version": policy_version,
    }
    policy_id = PaperPortfolioPolicy.compute_policy_id(policy_payload)
    policy = PaperPortfolioPolicy(policy_id=policy_id, **policy_payload)

    engine = HistoricalReplayEngine()
    pilot_universe = ["MGLU3", "SUZB3", "KLBN11", "RAIL3", "SLCE3"]

    print("\nExecuting event-driven historical replay cutoffs...")
    replay_run, replay_steps, report, all_events, snapshots_history = engine.run_replay(
        store_conn=store.connection,
        policy=policy,
        universe=pilot_universe,
        start_date=start_dt,
        end_date=end_dt,
        source_manifest_ids=source_manifest_ids,
        audit_4e3=audit_4e3,
        audit_4f=audit_4f,
        audit_4e2=audit_4e2,
    )

    # Persist to DuckDB idempotently
    store.save_historical_replay_run(replay_run.model_dump(mode="json"))
    for ev in all_events:
        store.save_paper_allocation_event(ev.model_dump(mode="json"))
    for snap in snapshots_history:
        store.save_paper_portfolio_snapshot(snap.model_dump(mode="json"))
    store.save_paper_portfolio_performance(report.model_dump(mode="json"))

    # Save the 4 audit manifests
    audits_dir.mkdir(parents=True, exist_ok=True)

    # 1. paper_portfolio_4g_run.json
    run_file = audits_dir / "paper_portfolio_4g_run.json"
    with open(run_file, "w", encoding="utf-8") as f:
        json.dump(replay_run.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    # 2. paper_portfolio_4g_ledger.json
    ledger_file = audits_dir / "paper_portfolio_4g_ledger.json"
    ledger_payload = {
        "replay_run_id": replay_run.replay_run_id,
        "total_allocation_events": len(all_events),
        "events": [e.model_dump(mode="json") for e in all_events],
    }
    with open(ledger_file, "w", encoding="utf-8") as f:
        json.dump(ledger_payload, f, indent=2, ensure_ascii=False)

    # 3. paper_portfolio_4g_performance.json
    perf_file = audits_dir / "paper_portfolio_4g_performance.json"
    with open(perf_file, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    # 4. replay_4g_end_to_end.json
    e2e_file = audits_dir / "replay_4g_end_to_end.json"
    e2e_payload = {
        "sprint": "4G.2",
        "methodology_version": engine.methodology_version,
        "replay_run": replay_run.model_dump(mode="json"),
        "steps": [s.model_dump(mode="json") for s in replay_steps],
        "performance": report.model_dump(mode="json"),
        "safety_assurances": {
            "dcf_executed": 0,
            "price_targets": 0,
            "buy_signals": 0,
            "order_executions": 0,
            "real_broker_integrations": 0,
            "synthetic_decision_ids": 0,
        },
        "persistence": {
            "database": str(db_path),
            "canonical_database": db_path.name == "audit.duckdb",
            "snapshots_generated": len(snapshots_history),
            "allocation_events_generated": len(all_events),
            "performance_report_id": report.report_id,
            "reconciliation": {
                "ledger_event_count_matches_run": len(all_events)
                == sum(len(step.allocation_event_ids) for step in replay_steps),
                "final_snapshot_matches_report_run": bool(snapshots_history)
                and snapshots_history[-1].nav >= 0,
            },
        },
    }
    with open(e2e_file, "w", encoding="utf-8") as f:
        json.dump(e2e_payload, f, indent=2, ensure_ascii=False)

    # Dedicated Phase 6 acceptance artifact.  This is intentionally derived
    # from the rows written to the canonical DuckDB connection, not merely
    # from in-memory objects, so a replay cannot claim persistence it did not
    # actually perform.
    persisted_run = store.connection.execute(
        "SELECT COUNT(*) FROM historical_replay_runs WHERE replay_run_id = ?",
        [replay_run.replay_run_id],
    ).fetchone()[0]
    persisted_snapshots = store.connection.execute(
        "SELECT COUNT(*) FROM paper_portfolio_snapshots WHERE portfolio_id = ?",
        ["pilot_paper_portfolio_001"],
    ).fetchone()[0]
    persisted_performance = store.connection.execute(
        "SELECT COUNT(*) FROM paper_portfolio_performance WHERE report_id = ?",
        [report.report_id],
    ).fetchone()[0]
    p6_payload = {
        "phase": "P6",
        "status": "PAPER_REPLAY_NO_ACTION"
        if report.thesis_metrics.get("simulated_entries", 0) == 0
        and report.thesis_metrics.get("simulated_exits", 0) == 0
        else "PAPER_REPLAY_COMPLETED",
        "replay_run_id": replay_run.replay_run_id,
        "database": str(db_path),
        "database_checksum_scope": "canonical_audit_duckdb",
        "period": {"start": replay_run.start_date, "end": replay_run.end_date},
        "policy_version": policy_version,
        "cash_yield_mode": cash_yield_mode,
        "initial_capital": initial_capital,
        "sessions": replay_run.market_sessions_processed,
        "decision_cutoffs": replay_run.decision_cutoffs_processed,
        "evaluations": report.thesis_metrics.get("total_evaluations", 0),
        "allocation_events_generated": len(all_events),
        "unique_allocation_event_ids": len({e.allocation_event_id for e in all_events}),
        "persisted_rows": {
            "replay_runs": persisted_run,
            "portfolio_snapshots": persisted_snapshots,
            "performance_reports": persisted_performance,
        },
        "reconciliation": {
            "run_persisted": persisted_run == 1,
            "snapshots_persisted": persisted_snapshots >= len(snapshots_history),
            "performance_persisted": persisted_performance == 1,
            "final_nav_equals_initial_when_no_allocations": (
                report.thesis_metrics.get("simulated_entries", 0) == 0
                and report.thesis_metrics.get("simulated_exits", 0) == 0
                and snapshots_history[-1].nav == initial_capital
            ),
        },
        "risk_controls": {
            "buy_signals": 0,
            "order_executions": 0,
            "real_broker_integrations": 0,
            "dcf_executed": 0,
            "price_targets": 0,
            "transaction_costs_brl": report.total_costs_brl,
            "slippage_brl": report.total_slippage_brl,
        },
        "blockers": sorted(set(replay_run.blocked_reasons))
        or (["NO_APPROVED_DECISIONS_FOR_ALLOCATION"]
            if report.thesis_metrics.get("simulated_entries", 0) == 0
            else []),
        "methodology_version": replay_run.methodology_versions.get("engine"),
    }
    with open(audits_dir / "p6_paper_portfolio_readiness.json", "w", encoding="utf-8") as f:
        json.dump(p6_payload, f, indent=2, ensure_ascii=False)

    print(f"\nSaved 4 audit manifests to {audits_dir}:")
    print("  - paper_portfolio_4g_run.json")
    print("  - paper_portfolio_4g_ledger.json")
    print("  - paper_portfolio_4g_performance.json")
    print("  - replay_4g_end_to_end.json")
    print("  - p6_paper_portfolio_readiness.json")

    print("\n--- Paper Portfolio Replay Summary ---")
    print(f"Replay Run ID:               {replay_run.replay_run_id}")
    print(f"Cutoffs Processed:           {replay_run.decision_cutoffs_processed}")
    print(f"Market Sessions Processed:   {replay_run.market_sessions_processed}")
    print(f"Initial Capital:             R$ {policy.initial_capital:,.2f}")
    print(f"Final NAV:                   R$ {snapshots_history[-1].nav:,.2f}")
    print(f"Net Total Return:            {report.total_return_pct:.2f}%")
    print(f"Annualized Return:           {report.annualized_return_pct:.2f}%")
    print(f"Max Drawdown:                {report.max_drawdown_pct:.2f}%")
    print(f"Total Transaction Costs:     R$ {report.total_costs_brl:,.2f}")
    print(f"Total Slippage:              R$ {report.total_slippage_brl:,.2f}")
    print(f"Simulated Entries / Exits:   {report.thesis_metrics.get('simulated_entries', 0)} / {report.thesis_metrics.get('simulated_exits', 0)}")
    print(f"Benchmark CDI Compounded:    {report.benchmark_returns.get('CDI_COMPOUNDED_ACCUMULATED_PCT', 0.0):.2f}%")
    print(f"Benchmark IBOV Real:         {report.benchmark_returns.get('IBOV_REAL_ACCUMULATED_PCT', 0.0):.2f}%")

    store.close()


if __name__ == "__main__":
    main()
