"""Resume a completed sidecar report after a client-side timeout.

This command only consumes a report exposed by ``/api/report/list`` with
status=completed. It never creates a report, scenario, or hypothesis locally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_b3_bot.adapters.mirofish import MiroFishClient
from macro_b3_bot.application.mirofish_scenario_engine import MiroFishScenarioEngine
from macro_b3_bot.config import Settings
from macro_b3_bot.domain.mirofish_scenario_models import (
    MiroFishSimulationRun,
    ScenarioSeedPackage,
    ScenarioSet,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: recover_mirofish_completed_run.py <simulation_id> <audit_json>")
    simulation_id, audit_path = sys.argv[1:]
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    seed = ScenarioSeedPackage.model_validate(audit["seed_package"])

    settings = Settings()
    client = MiroFishClient(base_url=settings.mirofish_base_url)
    reports = client.list_reports(simulation_id=simulation_id).get("reports", [])
    completed = [
        report for report in reports
        if isinstance(report, dict)
        and str(report.get("status", "")).lower() in {"completed", "success"}
    ]
    if not completed:
        raise RuntimeError("NO_COMPLETED_SIDECAR_REPORT_EXPOSED")
    report = completed[-1]
    report_id = report.get("report_id")
    if not report_id:
        raise RuntimeError("COMPLETED_REPORT_ID_NOT_EXPOSED")

    canonical = json.dumps(report, sort_keys=True, ensure_ascii=False, default=str)
    checksum = sha256(canonical.encode("utf-8")).hexdigest()
    raw_path = Path(f"data/raw/mirofish/reports/{checksum}.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(canonical, encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    run_payload = {
        "seed_package_id": seed.seed_package_id,
        "mirofish_project_id": report.get("project_id", "NOT_EXPOSED_BY_SERVICE"),
        "mirofish_graph_id": report.get("graph_id", "NOT_EXPOSED_BY_SERVICE"),
        "mirofish_simulation_id": simulation_id,
        "requested_at": report.get("created_at", now),
        "completed_at": report.get("completed_at", now),
        "service_version": "NOT_EXPOSED_BY_SERVICE",
        "model_information": "NOT_EXPOSED_BY_SERVICE",
        "configuration": {"recovery": "COMPLETED_REPORT_LIST", "source_report_id": report_id},
        "random_seed": "NOT_EXPOSED_BY_SERVICE",
        "prompt_hash": sha256(str(report.get("simulation_requirement", "")).encode()).hexdigest(),
        "input_checksum": seed.seed_file_checksum,
        "status": "SUCCESS",
        "raw_report_ids": [str(report_id)],
        "raw_response_checksum": checksum,
        "execution_classification": "CONTROLLED_SIDECAR_HOMOLOGATION",
        "methodology_version": "5A.3-mirofish-engine-v3",
    }
    run_id = MiroFishSimulationRun.compute_run_id(run_payload)
    engine = MiroFishScenarioEngine(client=client)
    hypotheses = engine._parse_mirofish_report_to_hypotheses(report, run_id, seed)
    run_payload["status"] = "SUCCESS" if hypotheses else "FAILED_UNSUPPORTED_REPORT_SCHEMA"
    run = MiroFishSimulationRun(
        simulation_run_id=run_id,
        **run_payload,
    )
    set_payload = {
        "event_id": seed.material_event_ids[0] if seed.material_event_ids else "GENERAL_MACRO_CUTOFF",
        "as_of_timestamp": seed.as_of_timestamp,
        "scenario_hypothesis_ids": [h.hypothesis_id for h in hypotheses],
        "coverage_summary": "RECOVERED_COMPLETED_SIDECAR_REPORT",
        "contradiction_summary": "NOT_EXECUTED",
        "missing_variables": [] if hypotheses else ["UNSUPPORTED_REPORT_SCHEMA"],
        "methodology_version": "5A.3-scenario-set-v3",
        "created_at": now,
    }
    scenario_set = ScenarioSet(
        scenario_set_id=ScenarioSet.compute_scenario_set_id(set_payload),
        **set_payload,
    )

    store = DatabaseStore(settings.data_dir / "macro_b3_bot.duckdb")
    store.save_scenario_seed_package(seed.model_dump(mode="json"))
    store.save_raw_mirofish_report({
        "report_id": str(report_id), "simulation_id": simulation_id,
        "project_id": report.get("project_id", ""), "content_checksum": checksum,
        "byte_size": len(canonical.encode("utf-8")), "mime_type": "application/json",
        "retrieved_at": now, "source_endpoint": "/api/report/list",
        "file_path": str(raw_path), "canonical_payload_json": canonical,
    })
    store.save_mirofish_simulation_run(run.model_dump(mode="json"))
    store.save_scenario_set(scenario_set.model_dump(mode="json"))
    for hypothesis in hypotheses:
        store.save_scenario_hypothesis(hypothesis.model_dump(mode="json"))
    store.close()
    client.close()

    audits = Path("data/audits")
    (audits / "mirofish_5a_simulation_runs.json").write_text(
        json.dumps({"total_runs": 1, "runs": [run.model_dump(mode="json")]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (audits / "mirofish_5a_scenario_sets.json").write_text(
        json.dumps({"total_scenario_sets": 1, "scenario_sets": [scenario_set.model_dump(mode="json")],
                    "hypotheses": [h.model_dump(mode="json") for h in hypotheses]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"simulation_run_id": run_id, "status": run.status,
                      "report_id": report_id, "hypotheses": len(hypotheses),
                      "report_checksum": checksum}, ensure_ascii=False))


if __name__ == "__main__":
    main()
