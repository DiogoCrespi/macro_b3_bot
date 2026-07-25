"""Sprint 5A.2 MiroFish Scenario Engine.

Orchestrates Point-In-Time seed package creation, HTTP interaction with MiroFish sidecar,
and generation of structured ScenarioSets containing typed ScenarioHypotheses marked UNVERIFIED.

Strict Seed & Sidecar Execution:
- Mandatory PIT data integrity (available_at <= --as-of)
- Seed package materialization as content-addressed Markdown/TXT
- Healthcheck validation (HTTP 200 OK + valid JSON schema)
- Full sidecar lifecycle: generate_ontology -> create_simulation -> list_reports
- Zero local hardcoded hypotheses on offline/failed/blocked runs
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from macro_b3_bot.adapters.mirofish import MiroFishClient
from macro_b3_bot.domain.mirofish_scenario_models import (
    MiroFishSimulationRun,
    ScenarioHypothesis,
    ScenarioSeedPackage,
    ScenarioSet,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


class MiroFishScenarioEngine:
    """
    Scenario generation engine integrating MiroFish generative sidecar as an unverified hypothesis generator.
    """
    methodology_version = "5A.2-mirofish-engine-v2"

    def __init__(self, client: MiroFishClient | None = None, store: DatabaseStore | None = None):
        self.client = client
        self.store = store

    def generate_scenarios_for_cutoff(
        self,
        *,
        cutoff_dt: datetime,
        macro_releases_pit: list[dict[str, Any]] | None = None,
        evidence_claims_pit: list[dict[str, Any]] | None = None,
        sector_state_snapshots_pit: list[dict[str, Any]] | None = None,
        macro_regime_snapshots_pit: list[dict[str, Any]] | None = None,
        macro_event_candidates_pit: list[dict[str, Any]] | None = None,
        source_documents_pit: list[dict[str, Any]] | None = None,
        causal_graph_version_pit: str | None = None,
        known_actors: list[str] | None = None,
    ) -> tuple[ScenarioSeedPackage, MiroFishSimulationRun, ScenarioSet, list[ScenarioHypothesis]]:
        if self.store is None and (
            macro_releases_pit is None
            and evidence_claims_pit is None
            and sector_state_snapshots_pit is None
            and source_documents_pit is None
        ):
            raise ValueError("DatabaseStore must be provided to MiroFishScenarioEngine for PIT data loading.")

        as_of_str = cutoff_dt.isoformat()

        # Load PIT data using store if not directly supplied
        if macro_releases_pit is None and self.store is not None:
            macro_releases_pit = self.store.get_macro_releases_pit(cutoff_dt)
        else:
            macro_releases_pit = macro_releases_pit or []

        if evidence_claims_pit is None and self.store is not None:
            evidence_claims_pit = self.store.get_evidence_claims_pit(cutoff_dt)
        else:
            evidence_claims_pit = evidence_claims_pit or []

        if sector_state_snapshots_pit is None and self.store is not None:
            sector_state_snapshots_pit = self.store.get_sector_state_snapshots_pit(cutoff_dt)
        else:
            sector_state_snapshots_pit = sector_state_snapshots_pit or []

        if macro_regime_snapshots_pit is None and self.store is not None:
            macro_regime_snapshots_pit = self.store.get_macro_regime_snapshots_pit(cutoff_dt)
        else:
            macro_regime_snapshots_pit = macro_regime_snapshots_pit or []

        if source_documents_pit is None and self.store is not None:
            source_documents_pit = self.store.get_source_documents_pit(cutoff_dt)
        else:
            source_documents_pit = source_documents_pit or []

        if causal_graph_version_pit is None and self.store is not None:
            getter = getattr(self.store, "get_causal_graph_version_pit", None)
            if callable(getter):
                val = getter(cutoff_dt)
                causal_graph_version_pit = str(val) if isinstance(val, str) else "1.0.0"
            else:
                causal_graph_version_pit = "1.0.0"
        else:
            causal_graph_version_pit = str(causal_graph_version_pit) if causal_graph_version_pit else "1.0.0"

        # Extract real IDs from PIT data (never fabricate IDs)
        material_event_ids = [str(m["release_id"]) for m in macro_releases_pit if "release_id" in m]
        evidence_claim_ids = [str(e["claim_id"]) for e in evidence_claims_pit if "claim_id" in e]
        macro_state_ids = [str(m["snapshot_id"]) for m in macro_regime_snapshots_pit if "snapshot_id" in m]
        sector_state_ids = [str(s["snapshot_id"]) for s in sector_state_snapshots_pit if "snapshot_id" in s]
        source_document_ids = [str(d["document_id"]) for d in source_documents_pit if "document_id" in d]

        known_actors = known_actors or ["BCB", "FED", "MINISTRY_OF_FINANCE", "B3_EXCHANGE", "CVM"]
        source_input_ids = (
            material_event_ids
            + evidence_claim_ids
            + macro_state_ids
            + sector_state_ids
            + source_document_ids
        )

        # Base seed payload without path/checksum for initial ID computation
        seed_payload_base = {
            "as_of_timestamp": as_of_str,
            "material_event_ids": material_event_ids,
            "evidence_claim_ids": evidence_claim_ids,
            "macro_state_ids": macro_state_ids,
            "sector_state_ids": sector_state_ids,
            "known_actor_ids": known_actors,
            "causal_graph_version": causal_graph_version_pit,
            "source_document_ids": source_document_ids,
            "prompt_template_version": "5A.2-mirofish-seed-v2",
            "mime_type": "text/markdown",
            "source_input_ids": source_input_ids,
        }
        seed_id = ScenarioSeedPackage.compute_seed_id(seed_payload_base)

        # Check for empty PIT seed
        if not source_input_ids:
            seed_package = ScenarioSeedPackage(
                seed_package_id=seed_id,
                seed_file_path="BLOCKED_EMPTY_PIT_SEED",
                seed_file_checksum="BLOCKED_EMPTY_PIT_SEED",
                **seed_payload_base,
            )
            run_payload = {
                "seed_package_id": seed_id,
                "mirofish_project_id": "BLOCKED_EMPTY_PIT_SEED",
                "mirofish_graph_id": "BLOCKED_EMPTY_PIT_SEED",
                "mirofish_simulation_id": "BLOCKED_EMPTY_PIT_SEED",
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "service_version": "mirofish-v1.0-blocked",
                "model_information": "BLOCKED_EMPTY_PIT_SEED",
                "configuration": {"empty_pit_seed": True},
                "random_seed": "BLOCKED_EMPTY_PIT_SEED",
                "prompt_hash": "",
                "input_checksum": "",
                "status": "BLOCKED_EMPTY_PIT_SEED",
                "raw_report_ids": [],
                "raw_response_checksum": "BLOCKED_EMPTY_PIT_SEED",
                "methodology_version": self.methodology_version,
            }
            run_id = MiroFishSimulationRun.compute_run_id(run_payload)
            sim_run = MiroFishSimulationRun(simulation_run_id=run_id, **run_payload)

            set_payload = {
                "event_id": "BLOCKED_EMPTY_PIT_SEED",
                "as_of_timestamp": as_of_str,
                "scenario_hypothesis_ids": [],
                "coverage_summary": f"Scenario generation blocked due to empty PIT seed for cutoff {as_of_str}.",
                "contradiction_summary": "CONTRADICTION_ANALYSIS_NOT_EXECUTED",
                "missing_variables": ["REAL_PIT_DATA", "REALTIME_MIROFISH_INTERACTION"],
                "methodology_version": "5A.2-scenario-set-v2",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            set_id = ScenarioSet.compute_scenario_set_id(set_payload)
            scenario_set = ScenarioSet(scenario_set_id=set_id, **set_payload)

            return seed_package, sim_run, scenario_set, []

        # Materialize seed file content
        temp_seed_pkg = ScenarioSeedPackage(seed_package_id=seed_id, **seed_payload_base)
        seed_file_content = self._generate_mirofish_seed_file(
            temp_seed_pkg,
            macro_releases_pit,
            evidence_claims_pit,
            sector_state_snapshots_pit,
            source_documents_pit,
        )
        seed_file_checksum = sha256(seed_file_content.encode("utf-8")).hexdigest()

        seed_file_path = Path(f"data/mirofish_seeds/{seed_id}.md")
        seed_file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(seed_file_path, "w", encoding="utf-8") as f:
            f.write(seed_file_content)

        seed_package = ScenarioSeedPackage(
            seed_package_id=seed_id,
            seed_file_path=str(seed_file_path),
            seed_file_checksum=seed_file_checksum,
            **seed_payload_base,
        )

        prompt_str = f"Simulate macro and sector scenarios as of {as_of_str} with {len(material_event_ids)} macro events, {len(evidence_claim_ids)} evidence claims, and {len(sector_state_snapshots_pit)} sector states."
        prompt_hash = sha256(prompt_str.encode("utf-8")).hexdigest()
        input_checksum = seed_file_checksum
        requested_at_str = datetime.now(timezone.utc).isoformat()

        # Check service health
        is_online = False
        if self.client is not None:
            try:
                is_online = self.client.healthcheck()
            except Exception:
                is_online = False

        if not is_online:
            run_payload = {
                "seed_package_id": seed_id,
                "mirofish_project_id": "NOT_EXPOSED_BY_SERVICE",
                "mirofish_graph_id": "NOT_EXPOSED_BY_SERVICE",
                "mirofish_simulation_id": "NOT_EXPOSED_BY_SERVICE",
                "requested_at": requested_at_str,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "service_version": "mirofish-v1.0-offline",
                "model_information": "NOT_EXPOSED_BY_SERVICE",
                "configuration": {"offline_fallback": True},
                "random_seed": "NOT_EXPOSED_BY_SERVICE",
                "prompt_hash": prompt_hash,
                "input_checksum": input_checksum,
                "status": "SERVICE_OFFLINE",
                "raw_report_ids": [],
                "raw_response_checksum": "SERVICE_OFFLINE_NO_RESPONSE",
                "methodology_version": self.methodology_version,
            }
            run_id = MiroFishSimulationRun.compute_run_id(run_payload)
            sim_run = MiroFishSimulationRun(simulation_run_id=run_id, **run_payload)

            set_payload = {
                "event_id": material_event_ids[0] if material_event_ids else "GENERAL_MACRO_CUTOFF",
                "as_of_timestamp": as_of_str,
                "scenario_hypothesis_ids": [],
                "coverage_summary": f"Offline scenario run for cutoff {as_of_str}. Zero hypotheses generated.",
                "contradiction_summary": "CONTRADICTION_ANALYSIS_NOT_EXECUTED",
                "missing_variables": ["REALTIME_MIROFISH_INTERACTION"],
                "methodology_version": "5A.2-scenario-set-v2",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            set_id = ScenarioSet.compute_scenario_set_id(set_payload)
            scenario_set = ScenarioSet(scenario_set_id=set_id, **set_payload)

            return seed_package, sim_run, scenario_set, []

        # Online HTTP execution
        assert self.client is not None
        try:
            # 1. Generate ontology / graph
            res = self.client.generate_ontology(
                [str(seed_file_path)],
                prompt_str,
                project_name=f"proj_{int(cutoff_dt.timestamp())}",
            )
            project_id = res.get("project_id")
            graph_id = res.get("graph_id")

            if not (project_id and graph_id):
                raise ValueError("MiroFish generate_ontology response missing project_id or graph_id.")

            # 2. Create simulation
            sim_res = self.client.create_simulation(project_id, graph_id, config={})
            simulation_id = sim_res.get("simulation_id")

            if not simulation_id:
                raise ValueError("MiroFish create_simulation response missing simulation_id.")

            # 3. Retrieve reports
            reports_res = self.client.list_reports(project_id=project_id, simulation_id=simulation_id)
            reports = reports_res.get("reports", [])

            if not reports:
                raise ValueError("MiroFish list_reports returned no reports.")

            raw_report = reports[0]
            raw_report_id = str(raw_report.get("report_id", f"rep_{simulation_id}"))
            raw_report_checksum = sha256(
                json.dumps(raw_report, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()

            # Compute preliminary run ID for scenario hypotheses
            prelim_run_payload = {
                "seed_package_id": seed_id,
                "mirofish_project_id": project_id,
                "mirofish_graph_id": graph_id,
                "mirofish_simulation_id": simulation_id,
                "requested_at": requested_at_str,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "service_version": "mirofish-v1.0-online",
                "model_information": str(res.get("model", "NOT_EXPOSED_BY_SERVICE")),
                "configuration": res.get("config", {}),
                "random_seed": str(res.get("seed", "NOT_EXPOSED_BY_SERVICE")),
                "prompt_hash": prompt_hash,
                "input_checksum": input_checksum,
                "status": "SUCCESS",
                "raw_report_ids": [raw_report_id],
                "raw_response_checksum": raw_report_checksum,
                "methodology_version": self.methodology_version,
            }
            run_id = MiroFishSimulationRun.compute_run_id(prelim_run_payload)
            sim_run = MiroFishSimulationRun(simulation_run_id=run_id, **prelim_run_payload)

            # 4. Parse hypotheses from report
            hypotheses = self._parse_mirofish_report_to_hypotheses(raw_report, run_id)
            hyp_ids = [h.hypothesis_id for h in hypotheses]

            set_payload = {
                "event_id": material_event_ids[0] if material_event_ids else "GENERAL_MACRO_CUTOFF",
                "as_of_timestamp": as_of_str,
                "scenario_hypothesis_ids": hyp_ids,
                "coverage_summary": f"Online MiroFish simulation set completed for cutoff {as_of_str}.",
                "contradiction_summary": "CONTRADICTION_ANALYSIS_NOT_EXECUTED",
                "missing_variables": [],
                "methodology_version": "5A.2-scenario-set-v2",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            set_id = ScenarioSet.compute_scenario_set_id(set_payload)
            scenario_set = ScenarioSet(scenario_set_id=set_id, **set_payload)

            return seed_package, sim_run, scenario_set, hypotheses

        except Exception as e:
            run_payload = {
                "seed_package_id": seed_id,
                "mirofish_project_id": "NOT_EXPOSED_BY_SERVICE",
                "mirofish_graph_id": "NOT_EXPOSED_BY_SERVICE",
                "mirofish_simulation_id": "NOT_EXPOSED_BY_SERVICE",
                "requested_at": requested_at_str,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "service_version": "mirofish-v1.0-online",
                "model_information": "NOT_EXPOSED_BY_SERVICE",
                "configuration": {"network_error_fallback": True, "error_message": str(e)},
                "random_seed": "NOT_EXPOSED_BY_SERVICE",
                "prompt_hash": prompt_hash,
                "input_checksum": input_checksum,
                "status": "FAILED_INCOMPLETE_SERVICE_RUN",
                "raw_report_ids": [],
                "raw_response_checksum": "FAILED_INCOMPLETE_SERVICE_RUN",
                "methodology_version": self.methodology_version,
            }
            run_id = MiroFishSimulationRun.compute_run_id(run_payload)
            sim_run = MiroFishSimulationRun(simulation_run_id=run_id, **run_payload)

            set_payload = {
                "event_id": material_event_ids[0] if material_event_ids else "GENERAL_MACRO_CUTOFF",
                "as_of_timestamp": as_of_str,
                "scenario_hypothesis_ids": [],
                "coverage_summary": f"Online scenario set failed for cutoff {as_of_str}: {e}",
                "contradiction_summary": "CONTRADICTION_ANALYSIS_NOT_EXECUTED",
                "missing_variables": ["REALTIME_MIROFISH_INTERACTION"],
                "methodology_version": "5A.2-scenario-set-v2",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            set_id = ScenarioSet.compute_scenario_set_id(set_payload)
            scenario_set = ScenarioSet(scenario_set_id=set_id, **set_payload)

            return seed_package, sim_run, scenario_set, []

    def _generate_mirofish_seed_file(
        self,
        seed_package: ScenarioSeedPackage,
        macro_releases: list[dict[str, Any]],
        evidence_claims: list[dict[str, Any]],
        sector_states: list[dict[str, Any]],
        source_documents: list[dict[str, Any]],
    ) -> str:
        """
        Generates structured Markdown seed file content for MiroFish ingestion.
        """
        content = [
            f"# MiroFish Scenario Seed Package: {seed_package.seed_package_id}",
            f"## As Of Timestamp: {seed_package.as_of_timestamp}",
            f"## Causal Graph Version: {seed_package.causal_graph_version}",
            f"## Prompt Template Version: {seed_package.prompt_template_version}",
            "\n### Macro Releases (material_event_ids):",
        ]
        if macro_releases:
            for mr in macro_releases:
                content.append(
                    f"- ID: {mr.get('release_id')}, Indicator: {mr.get('indicator')}, Value: {mr.get('actual_value')}, Available: {mr.get('available_at')}"
                )
        else:
            content.append("- No macro releases found.")

        content.append("\n### Evidence Claims (evidence_claim_ids):")
        if evidence_claims:
            for ec in evidence_claims:
                content.append(
                    f"- ID: {ec.get('claim_id')}, Subject: {ec.get('subject')}, Predicate: {ec.get('predicate')}, Object: {ec.get('object_text')}, Created: {ec.get('created_at')}"
                )
        else:
            content.append("- No evidence claims found.")

        content.append("\n### Sector State Snapshots (sector_state_ids):")
        if sector_states:
            for ss in sector_states:
                content.append(
                    f"- ID: {ss.get('snapshot_id')}, Sector: {ss.get('sector')}, Net Impact: {ss.get('net_impact')}, As Of: {ss.get('as_of_timestamp')}"
                )
        else:
            content.append("- No sector state snapshots found.")

        content.append("\n### Source Documents (source_document_ids):")
        if source_documents:
            for sd in source_documents:
                doc_type = sd.get("document_type", sd.get("source_type", "DOC"))
                content.append(
                    f"- ID: {sd.get('document_id')}, Type: {doc_type}, Available: {sd.get('available_at')}"
                )
        else:
            content.append("- No source documents found.")

        content.append(f"\n### Known Actors: {', '.join(seed_package.known_actor_ids)}")
        content.append("\n--- END OF SEED PACKAGE ---")
        return "\n".join(content)

    def _parse_mirofish_report_to_hypotheses(
        self, raw_report: dict[str, Any], run_id: str
    ) -> list[ScenarioHypothesis]:
        """
        Parses a raw MiroFish report dictionary into typed ScenarioHypothesis objects.
        Preserves report section excerpts, raw report ID, checksum, and unverified status.
        """
        scenarios_from_report = (
            raw_report.get("scenarios") or raw_report.get("hypotheses") or []
        )
        raw_report_id = str(raw_report.get("report_id", "NOT_EXPOSED_BY_SERVICE"))
        raw_report_checksum = sha256(
            json.dumps(raw_report, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        hypotheses = []
        for scenario_data in scenarios_from_report:
            if not isinstance(scenario_data, dict):
                continue

            stype = scenario_data.get("type", scenario_data.get("scenario_type", "UNKNOWN"))
            if stype not in ("BASE", "BULL", "BEAR", "CONTRARIAN", "TAIL", "UNKNOWN"):
                stype = "UNKNOWN"

            conf = scenario_data.get("confidence")
            conf_val = float(conf) if conf is not None else None

            excerpt = str(
                scenario_data.get(
                    "excerpt",
                    scenario_data.get("report_excerpt", scenario_data.get("trigger", "")),
                )
            )

            h_payload = {
                "simulation_run_id": run_id,
                "scenario_type": stype,
                "trigger": str(scenario_data.get("trigger", "")),
                "actors": list(scenario_data.get("actors", [])),
                "actions": list(scenario_data.get("actions", [])),
                "macro_factors": list(scenario_data.get("macro_factors", [])),
                "sector_effects": list(scenario_data.get("sector_effects", [])),
                "second_order_effects": list(scenario_data.get("second_order_effects", [])),
                "expected_horizon": str(scenario_data.get("expected_horizon", "MEDIUM_TERM")),
                "supporting_evidence_ids": list(scenario_data.get("supporting_evidence_ids", [])),
                "contradicting_evidence_ids": list(scenario_data.get("contradicting_evidence_ids", [])),
                "verification_status": "UNVERIFIED",
                "confidence": conf_val,
                "report_excerpt": excerpt,
                "raw_report_id": raw_report_id,
                "report_checksum": raw_report_checksum,
                "parser_version": "5A.2-mirofish-parser-v1",
            }
            h_id = ScenarioHypothesis.compute_hypothesis_id(h_payload)
            hypotheses.append(ScenarioHypothesis(hypothesis_id=h_id, **h_payload))

        if not hypotheses:
            raise ValueError("MiroFish report parsing yielded no hypotheses.")

        return hypotheses

    def _generate_fallback_hypotheses(
        self,
        run_id: str,
        cutoff_dt: datetime,
        seed_package: ScenarioSeedPackage,
    ) -> list[ScenarioHypothesis]:
        """
        Retained for API backwards compatibility. Returns empty list as per Sprint 5A.2 rules.
        """
        return []
