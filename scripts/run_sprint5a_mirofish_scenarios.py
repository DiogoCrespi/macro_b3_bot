"""
Sprint 5A: MiroFish Scenario Engine Integration

Orchestrates Point-In-Time seed package creation, HTTP interaction with MiroFish sidecar,
and generation of structured ScenarioSets containing typed ScenarioHypotheses marked UNVERIFIED.

Fault-tolerant: Handles offline HTTP service gracefully without failing pipeline.

Generates 3 audit artifacts:
- data/audits/mirofish_5a_simulation_runs.json
- data/audits/mirofish_5a_scenario_sets.json
- data/audits/mirofish_5a_audit.json
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
from macro_b3_bot.adapters.mirofish import MiroFishClient
from macro_b3_bot.application.mirofish_scenario_engine import MiroFishScenarioEngine


def parse_cli_args() -> datetime:
    as_of_str = "2026-07-24T00:00:00Z"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("--as-of="):
            as_of_str = arg.split("=", 1)[1]
        elif arg == "--as-of" and i < len(sys.argv) - 1:
            as_of_str = sys.argv[i + 1]

    dt = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    print("=== Sprint 5A: MiroFish Scenario Engine Integration ===")
    as_of_dt = parse_cli_args()
    print(f"Scenario Cutoff (as-of): {as_of_dt.isoformat()}")

    settings = Settings()
    db_path = settings.data_dir / "macro_b3_bot.duckdb"
    store = DatabaseStore(db_path)

    # Instantiate MiroFish HTTP Client if configured
    client = None
    if getattr(settings, "mirofish_base_url", None):
        try:
            client = MiroFishClient(base_url=settings.mirofish_base_url)
        except Exception:
            client = None

    engine = MiroFishScenarioEngine(client=client)

    print("\nGenerating Point-In-Time scenario hypotheses...")
    seed_pkg, sim_run, scenario_set, hypotheses = engine.generate_scenarios_for_cutoff(
        cutoff_dt=as_of_dt
    )

    # Persist to DuckDB idempotently
    store.save_scenario_seed_package(seed_pkg.model_dump(mode="json"))
    store.save_mirofish_simulation_run(sim_run.model_dump(mode="json"))
    store.save_scenario_set(scenario_set.model_dump(mode="json"))

    # Save 3 audit manifests
    audits_dir = Path("data/audits")
    audits_dir.mkdir(parents=True, exist_ok=True)

    # 1. mirofish_5a_simulation_runs.json
    runs_file = audits_dir / "mirofish_5a_simulation_runs.json"
    with open(runs_file, "w", encoding="utf-8") as f:
        json.dump({
            "total_runs": 1,
            "runs": [sim_run.model_dump(mode="json")],
        }, f, indent=2, ensure_ascii=False)

    # 2. mirofish_5a_scenario_sets.json
    sets_file = audits_dir / "mirofish_5a_scenario_sets.json"
    with open(sets_file, "w", encoding="utf-8") as f:
        json.dump({
            "total_scenario_sets": 1,
            "scenario_sets": [scenario_set.model_dump(mode="json")],
            "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
        }, f, indent=2, ensure_ascii=False)

    # 3. mirofish_5a_audit.json
    audit_file = audits_dir / "mirofish_5a_audit.json"
    audit_payload = {
        "sprint": "5A",
        "methodology_version": engine.methodology_version,
        "cutoff_timestamp": as_of_dt.isoformat(),
        "seed_package": seed_pkg.model_dump(mode="json"),
        "simulation_run": sim_run.model_dump(mode="json"),
        "scenario_set": scenario_set.model_dump(mode="json"),
        "total_hypotheses": len(hypotheses),
        "unverified_count": sum(1 for h in hypotheses if h.verification_status == "UNVERIFIED"),
        "safety_assurances": {
            "dcf_executed": 0,
            "price_targets": 0,
            "buy_signals": 0,
            "order_executions": 0,
            "unverified_hypotheses_influencing_decisions_directly": 0,
        },
    }
    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump(audit_payload, f, indent=2, ensure_ascii=False)

    print(f"\nSaved 3 audit manifests to {audits_dir}:")
    print("  - mirofish_5a_simulation_runs.json")
    print("  - mirofish_5a_scenario_sets.json")
    print("  - mirofish_5a_audit.json")

    print("\n--- MiroFish Scenario Engine Summary ---")
    print(f"Simulation Run ID:       {sim_run.simulation_run_id}")
    print(f"Run Status:              {sim_run.status}")
    print(f"Scenario Set ID:         {scenario_set.scenario_set_id}")
    print(f"Total Hypotheses:        {len(hypotheses)}")
    print(f"Unverified Hypotheses:   {sum(1 for h in hypotheses if h.verification_status == 'UNVERIFIED')}")
    print(f"Prompt Hash:             {sim_run.prompt_hash[:16]}...")
    print(f"Input Checksum:          {sim_run.input_checksum[:16]}...")

    if client:
        client.close()
    store.close()


if __name__ == "__main__":
    main()
