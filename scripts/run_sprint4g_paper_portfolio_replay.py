"""
Sprint 4G: Paper Portfolio & End-to-End Historical Replay

Orchestrates sequential, point-in-time historical replays without look-ahead bias.
Evaluates paper portfolio allocations, returns, drawdowns, transaction costs,
benchmark comparisons (IBOV, CDI, Equal-Weight Pilot Universe), thesis metrics, and blocker impacts.

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


def parse_cli_args() -> tuple[datetime, datetime, float, str]:
    start_str = "2024-01-01T00:00:00Z"
    end_str = "2026-07-24T00:00:00Z"
    capital = 100000.0
    policy_ver = "4G.1-paper-policy-v1"

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

    s_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    e_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    if s_dt.tzinfo is None:
        s_dt = s_dt.replace(tzinfo=timezone.utc)
    if e_dt.tzinfo is None:
        e_dt = e_dt.replace(tzinfo=timezone.utc)

    return s_dt, e_dt, capital, policy_ver


def main() -> None:
    print("=== Sprint 4G: Paper Portfolio & End-to-End Historical Replay ===")
    start_dt, end_dt, initial_capital, policy_version = parse_cli_args()
    print(f"Replay Interval: {start_dt.isoformat()} to {end_dt.isoformat()}")
    print(f"Initial Capital: R$ {initial_capital:,.2f} | Policy Version: {policy_version}")

    settings = Settings()
    db_path = settings.data_dir / "macro_b3_bot.duckdb"
    store = DatabaseStore(db_path)

    audits_dir = Path("data/audits")
    audit_4e3 = load_json_file(audits_dir / "research_4e3_decisions.json")
    audit_4f = load_json_file(audits_dir / "research_4f_timing_risk.json")
    audit_4e2 = load_json_file(audits_dir / "valuation_4e2_historical_reverse.json")

    policy_payload = {
        "initial_capital": initial_capital,
        "version": policy_version,
    }
    policy_id = PaperPortfolioPolicy.compute_policy_id(policy_payload)
    policy = PaperPortfolioPolicy(policy_id=policy_id, **policy_payload)

    engine = HistoricalReplayEngine()
    pilot_universe = ["MGLU3", "SUZB3", "KLBN11", "RAIL3", "SLCE3"]

    print("\nExecuting historical replay cutoffs...")
    replay_run, replay_steps, report, all_events, snapshots_history = engine.run_replay(
        store_conn=store.connection,
        policy=policy,
        universe=pilot_universe,
        start_date=start_dt,
        end_date=end_dt,
        audit_4e3=audit_4e3,
        audit_4f=audit_4f,
        audit_4e2=audit_4e2,
    )

    # Persist to DuckDB idempotently
    store.save_historical_replay_run(replay_run.model_dump(mode="json"))
    for ev in all_events:
        store.save_paper_allocation_event(ev.model_dump(mode="json"))

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
        "sprint": "4G",
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
        },
    }
    with open(e2e_file, "w", encoding="utf-8") as f:
        json.dump(e2e_payload, f, indent=2, ensure_ascii=False)

    print(f"\nSaved 4 audit manifests to {audits_dir}:")
    print("  - paper_portfolio_4g_run.json")
    print("  - paper_portfolio_4g_ledger.json")
    print("  - paper_portfolio_4g_performance.json")
    print("  - replay_4g_end_to_end.json")

    print("\n--- Paper Portfolio Replay Summary ---")
    print(f"Replay Run ID:               {replay_run.replay_run_id}")
    print(f"Cutoffs Processed:           {replay_run.decision_cutoffs_processed}")
    print(f"Initial Capital:             R$ {policy.initial_capital:,.2f}")
    print(f"Final NAV:                   R$ {snapshots_history[-1].nav:,.2f}")
    print(f"Net Total Return:            {report.total_return_pct:.2f}%")
    print(f"Annualized Return:           {report.annualized_return_pct:.2f}%")
    print(f"Max Drawdown:                {report.max_drawdown_pct:.2f}%")
    print(f"Total Transaction Costs:     R$ {report.total_costs_brl:,.2f}")
    print(f"Total Slippage:              R$ {report.total_slippage_brl:,.2f}")
    print(f"Simulated Entries / Exits:   {report.thesis_metrics.get('simulated_entries', 0)} / {report.thesis_metrics.get('simulated_exits', 0)}")
    print(f"Benchmark CDI Return:        {report.benchmark_returns.get('CDI_ACCUMULATED_PCT', 0.0):.2f}%")
    print(f"Benchmark IBOV Return:       {report.benchmark_returns.get('IBOV_PROXY_ACCUMULATED_PCT', 0.0):.2f}%")

    store.close()


if __name__ == "__main__":
    main()
