"""Sprint 5A domain models for MiroFish Scenario Engine Integration."""

from hashlib import sha256
import json
from typing import Any, Literal
from pydantic import BaseModel, Field


class ScenarioSeedPackage(BaseModel):
    """
    Point-In-Time input seed package for MiroFish simulation.
    Contains strictly evidence claims, macro state, and sector state available at cutoff.
    """
    seed_package_id: str
    as_of_timestamp: str
    material_event_ids: list[str] = Field(default_factory=list)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    macro_state_ids: list[str] = Field(default_factory=list)
    sector_state_ids: list[str] = Field(default_factory=list)
    known_actor_ids: list[str] = Field(default_factory=list)
    causal_graph_version: str = "NOT_EXPOSED"
    source_document_ids: list[str] = Field(default_factory=list)
    prompt_template_version: str = "5A.3-mirofish-seed-v3"
    seed_file_path: str = ""
    seed_file_checksum: str = ""
    mime_type: str = "text/markdown"
    source_input_ids: list[str] = Field(default_factory=list)
    loader_diagnostics: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def compute_seed_id(cls, payload: dict[str, Any]) -> str:
        canonical = {k: v for k, v in payload.items() if k != "seed_package_id"}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()


class MiroFishSimulationRun(BaseModel):
    """
    Manifest of a MiroFish simulation run.
    Contains HTTP service parameters, request/response hashes, and service status.
    Unexposed API parameters explicitly record 'NOT_EXPOSED_BY_SERVICE'.
    """
    simulation_run_id: str
    seed_package_id: str
    mirofish_project_id: str = "NOT_EXPOSED_BY_SERVICE"
    mirofish_graph_id: str = "NOT_EXPOSED_BY_SERVICE"
    mirofish_simulation_id: str = "NOT_EXPOSED_BY_SERVICE"
    requested_at: str
    completed_at: str = ""
    service_version: str = "NOT_EXPOSED_BY_SERVICE"
    model_information: str = "NOT_EXPOSED_BY_SERVICE"
    configuration: dict[str, Any] = Field(default_factory=dict)
    random_seed: str = "NOT_EXPOSED_BY_SERVICE"
    prompt_hash: str = ""
    input_checksum: str = ""
    status: Literal[
        "SUCCESS",
        "SERVICE_OFFLINE",
        "BLOCKED_EMPTY_PIT_SEED",
        "FAILED_UNSUPPORTED_REPORT_SCHEMA",
        "FAILED_INCOMPLETE_SERVICE_RUN",
        "FAILED_GRAPH_BUILD",
        "FAILED_SIMULATION_CONFIGURATION",
        "FAILED_SIMULATION_RUN",
        "FAILED_REPORT_GENERATION",
        "TIMEOUT_GRAPH_BUILD",
        "TIMEOUT_SIMULATION",
        "TIMEOUT_REPORT",
        "FAILED",
    ] = "SUCCESS"
    raw_report_ids: list[str] = Field(default_factory=list)
    raw_response_checksum: str = ""
    methodology_version: str = "5A.3-mirofish-run-v3"

    @classmethod
    def compute_run_id(cls, payload: dict[str, Any]) -> str:
        canonical = {k: v for k, v in payload.items() if k != "simulation_run_id"}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()


class ScenarioHypothesis(BaseModel):
    """
    Typed scenario hypothesis generated only from a native, validated MiroFish
    structured report. Narrative reports are never promoted locally.
    Always initialized with verification_status = 'UNVERIFIED'.
    Must NOT directly trigger WATCH, BUY, target price, or order placement.
    """
    hypothesis_id: str
    simulation_run_id: str
    scenario_type: Literal["BASE", "BULL", "BEAR", "CONTRARIAN", "TAIL", "UNKNOWN"] = "UNKNOWN"
    trigger: str = ""
    actors: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    macro_factors: list[str] = Field(default_factory=list)
    sector_effects: list[str] = Field(default_factory=list)
    second_order_effects: list[str] = Field(default_factory=list)
    expected_horizon: str = "MEDIUM_TERM"
    supporting_evidence_claim_ids: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    macro_event_ids: list[str] = Field(default_factory=list)
    sector_state_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    verification_status: Literal["UNVERIFIED", "PARTIALLY_SUPPORTED", "SUPPORTED", "CONTRADICTED", "REJECTED"] = "UNVERIFIED"
    confidence: float | None = None
    report_excerpt: str = ""
    raw_report_id: str = ""
    report_checksum: str = ""
    parser_version: str = "5A.3-mirofish-parser-v2"
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def compute_hypothesis_id(cls, payload: dict[str, Any]) -> str:
        canonical = {k: v for k, v in payload.items() if k != "hypothesis_id"}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()


class ScenarioSet(BaseModel):
    """
    Container aggregating all hypotheses generated for a specific cutoff date.
    """
    scenario_set_id: str
    event_id: str = "GENERAL_MACRO_CUTOFF"
    as_of_timestamp: str
    scenario_hypothesis_ids: list[str] = Field(default_factory=list)
    coverage_summary: str = ""
    contradiction_summary: str = "CONTRADICTION_ANALYSIS_NOT_EXECUTED"
    missing_variables: list[str] = Field(default_factory=list)
    methodology_version: str = "5A.3-scenario-set-v3"
    created_at: str = ""

    @classmethod
    def compute_scenario_set_id(cls, payload: dict[str, Any]) -> str:
        canonical = {k: v for k, v in payload.items() if k not in ("scenario_set_id", "created_at")}
        sorted_keys = json.dumps(canonical, sort_keys=True, default=str)
        return sha256(sorted_keys.encode("utf-8")).hexdigest()

