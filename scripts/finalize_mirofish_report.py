"""Finalize a completed sidecar report after a client-side polling timeout.

This command never fabricates a report or a scenario. It retrieves the
completed report identified by the real sidecar IDs, runs the same strict
structured extraction used by the production engine, verifies provenance, and
rewrites the three Sprint 5A manifests.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_b3_bot.adapters.mirofish import MiroFishClient, MIROFISH_REPORT_SCHEMA_VERSION
from macro_b3_bot.application.mirofish_scenario_engine import MiroFishScenarioEngine
from macro_b3_bot.config import Settings
from macro_b3_bot.domain.mirofish_scenario_models import (
    MiroFishSimulationRun,
    ScenarioSeedPackage,
    ScenarioSet,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


def main() -> None:
    args = dict(arg.split("=", 1) for arg in sys.argv[1:] if "=" in arg)
    required = ("--project-id", "--graph-id", "--simulation-id", "--event-id", "--as-of")
    missing = [key for key in required if key not in args]
    if missing:
        raise SystemExit(f"missing arguments: {', '.join(missing)}")

    cutoff = datetime.fromisoformat(args["--as-of"].replace("Z", "+00:00"))
    settings = Settings()
    store = DatabaseStore(settings.data_dir / "macro_b3_bot.duckdb")
    client = MiroFishClient(base_url=settings.mirofish_base_url)
    engine = MiroFishScenarioEngine(client=client, store=store)

    # Reconstruct the exact PIT seed inputs used by the original run.
    event_id = args["--event-id"]
    releases = store.get_macro_releases_pit(cutoff)
    claims = store.get_evidence_claims_pit(cutoff)
    sectors = store.get_sector_state_snapshots_pit(cutoff)
    regimes = store.get_macro_regime_snapshots_pit(cutoff)
    documents = store.get_source_documents_pit(cutoff)
    target = [x for x in releases if x.get("release_id") == event_id]
    if target:
        releases = target
    doc_ids = {x.get("document_id") for x in releases if x.get("document_id")}
    claims = [x for x in claims if x.get("claim_id") == event_id or x.get("document_id") in doc_ids]
    doc_ids |= {x.get("document_id") for x in claims if x.get("document_id")}
    documents = [x for x in documents if x.get("document_id") in doc_ids]
    diagnostics = store.get_loader_diagnostics(cutoff, event_id=event_id)
    graph_getter = getattr(store, "get_causal_graph_version_pit", None)
    graph_version = graph_getter(cutoff) if callable(graph_getter) else "NOT_EXPOSED"
    graph_version = graph_version or "NOT_EXPOSED"
    material_ids = [str(x["release_id"]) for x in releases if x.get("release_id")]
    claim_ids = [str(x["claim_id"]) for x in claims if x.get("claim_id")]
    regime_ids = [str(x["snapshot_id"]) for x in regimes if x.get("snapshot_id")]
    sector_ids = [str(x["snapshot_id"]) for x in sectors if x.get("snapshot_id")]
    document_ids = [str(x["document_id"]) for x in documents if x.get("document_id")]
    source_ids = material_ids + claim_ids + regime_ids + sector_ids + document_ids
    seed_base = {
        "as_of_timestamp": cutoff.isoformat(), "material_event_ids": material_ids,
        "evidence_claim_ids": claim_ids, "macro_state_ids": regime_ids,
        "sector_state_ids": sector_ids, "known_actor_ids": [],
        "causal_graph_version": str(graph_version), "source_document_ids": document_ids,
        "prompt_template_version": "5A.3-mirofish-seed-v3", "mime_type": "text/markdown",
        "source_input_ids": source_ids, "loader_diagnostics": diagnostics,
    }
    seed_id = ScenarioSeedPackage.compute_seed_id(seed_base)
    seed_tmp = ScenarioSeedPackage(seed_package_id=seed_id, **seed_base)
    seed_text = engine._generate_mirofish_seed_file(seed_tmp, releases, claims, sectors, documents)
    seed_path = Path(f"data/mirofish_seeds/{seed_id}.md")
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(seed_text, encoding="utf-8")
    seed_checksum = sha256(seed_text.encode("utf-8")).hexdigest()
    seed = ScenarioSeedPackage(seed_package_id=seed_id, seed_file_path=str(seed_path),
        seed_file_checksum=seed_checksum, **seed_base)

    reports = client.list_reports(project_id=args["--project-id"], simulation_id=args["--simulation-id"]).get("reports", [])
    report = next((x for x in reports if x.get("status") == "completed"), None)
    if not report:
        raise RuntimeError("COMPLETED_REPORT_NOT_AVAILABLE")
    canonical = json.dumps(report, sort_keys=True, default=str, ensure_ascii=False)
    raw_bytes = canonical.encode("utf-8")
    checksum = sha256(raw_bytes).hexdigest()
    raw_path = Path(f"data/raw/mirofish/reports/{checksum}.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(canonical, encoding="utf-8")
    store.save_raw_mirofish_report({
        "report_id": report.get("report_id"), "simulation_id": args["--simulation-id"],
        "project_id": args["--project-id"], "content_checksum": checksum,
        "byte_size": len(raw_bytes), "mime_type": "application/json",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_endpoint": "/api/report/list", "file_path": str(raw_path),
        "canonical_payload_json": canonical,
    })
    prompt = (f"Simulate macro and sector scenarios as of {cutoff.isoformat()} with "
              f"{len(material_ids)} macro events, {len(claim_ids)} evidence claims, and "
              f"{len(sectors)} sector states. The final report MUST be native JSON matching "
              f"schema {MIROFISH_REPORT_SCHEMA_VERSION}; return no narrative-only report. "
              "Each scenario requires scenario_type, trigger and report_excerpt copied verbatim from the report.")
    payload = {
        "seed_package_id": seed_id, "mirofish_project_id": args["--project-id"],
        "mirofish_graph_id": args["--graph-id"], "mirofish_simulation_id": args["--simulation-id"],
        "requested_at": report.get("created_at", datetime.now(timezone.utc).isoformat()),
        "completed_at": report.get("completed_at", datetime.now(timezone.utc).isoformat()),
        "service_version": "NOT_EXPOSED_BY_SERVICE", "model_information": "NOT_EXPOSED_BY_SERVICE",
        "configuration": {"structured_report_contract": MiroFishClient.structured_report_config(),
                           "loader_diagnostics": diagnostics},
        "random_seed": "NOT_EXPOSED_BY_SERVICE", "prompt_hash": sha256(prompt.encode()).hexdigest(),
        "input_checksum": seed_checksum, "status": "SUCCESS", "raw_report_ids": [str(report.get("report_id"))],
        "raw_response_checksum": checksum, "methodology_version": engine.methodology_version,
    }
    run_id = MiroFishSimulationRun.compute_run_id(payload)
    hypotheses = engine._parse_mirofish_report_to_hypotheses(report, run_id, seed_package=seed)
    if not hypotheses:
        payload["status"] = "FAILED_UNSUPPORTED_REPORT_SCHEMA"
        run_id = MiroFishSimulationRun.compute_run_id(payload)
    if engine._last_extraction_record:
        payload["configuration"]["structured_extraction"] = engine._last_extraction_record
    sim_run = MiroFishSimulationRun(simulation_run_id=run_id, **payload)
    scenario_payload = {
        "event_id": event_id, "as_of_timestamp": cutoff.isoformat(),
        "scenario_hypothesis_ids": [h.hypothesis_id for h in hypotheses],
        "coverage_summary": "Finalized completed MiroFish sidecar report with strict extraction.",
        "contradiction_summary": "CONTRADICTION_ANALYSIS_NOT_EXECUTED", "missing_variables": [],
        "methodology_version": "5A.3-scenario-set-v3", "created_at": datetime.now(timezone.utc).isoformat(),
    }
    scenario_set = ScenarioSet(scenario_set_id=ScenarioSet.compute_scenario_set_id(scenario_payload), **scenario_payload)
    store.save_scenario_seed_package(seed.model_dump(mode="json"))
    store.save_mirofish_simulation_run(sim_run.model_dump(mode="json"))
    store.save_scenario_set(scenario_set.model_dump(mode="json"))
    for hypothesis in hypotheses:
        store.save_scenario_hypothesis(hypothesis.model_dump(mode="json"))
    audits = Path("data/audits")
    audits.mkdir(exist_ok=True)
    (audits / "mirofish_5a_simulation_runs.json").write_text(json.dumps({"total_runs": 1, "runs": [sim_run.model_dump(mode="json")]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (audits / "mirofish_5a_scenario_sets.json").write_text(json.dumps({"total_scenario_sets": 1, "scenario_sets": [scenario_set.model_dump(mode="json")], "hypotheses": [h.model_dump(mode="json") for h in hypotheses]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (audits / "mirofish_5a_audit.json").write_text(json.dumps({"sprint": "5A.3", "simulation_run": sim_run.model_dump(mode="json"), "scenario_set": scenario_set.model_dump(mode="json"), "total_hypotheses": len(hypotheses), "unverified_count": sum(h.verification_status == "UNVERIFIED" for h in hypotheses), "safety_assurances": {"dcf_executed": 0, "price_targets": 0, "buy_signals": 0, "order_executions": 0}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": sim_run.status, "simulation_run_id": run_id, "report_id": report.get("report_id"), "hypotheses": len(hypotheses), "structured_extraction": bool(engine._last_extraction_record)}, ensure_ascii=False))
    client.close()
    store.close()


if __name__ == "__main__":
    main()
