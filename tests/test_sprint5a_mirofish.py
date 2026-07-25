import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx

from macro_b3_bot.adapters.mirofish import MiroFishClient, MIROFISH_REPORT_SCHEMA_VERSION
from macro_b3_bot.application.mirofish_scenario_engine import MiroFishScenarioEngine
from macro_b3_bot.domain.mirofish_scenario_models import (
    ScenarioSeedPackage,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


@pytest.fixture
def mock_store():
    store = MagicMock(spec=DatabaseStore)
    store.get_macro_releases_pit.return_value = []
    store.get_evidence_claims_pit.return_value = []
    store.get_sector_state_snapshots_pit.return_value = []
    store.get_macro_regime_snapshots_pit.return_value = []
    store.get_macro_event_candidates_pit.return_value = []
    store.get_source_documents_pit.return_value = []
    store.get_loader_diagnostics.return_value = {}
    return store


@pytest.fixture
def mock_client():
    client = MagicMock(spec=MiroFishClient)
    client.healthcheck.return_value = True
    client.generate_ontology.return_value = {
        "project_id": "test_proj_123",
        "graph_id": "test_graph_456",
        "model": "test_model_v1",
        "config": {"param1": "value1"},
        "seed": "12345",
    }
    client.create_simulation.return_value = {"simulation_id": "test_sim_789"}
    client.prepare_simulation.return_value = {"simulation_id": "test_sim_789", "status": "ready"}
    client.poll_prepare.return_value = {"simulation_id": "test_sim_789", "status": "ready"}
    client.start_simulation.return_value = {"simulation_id": "test_sim_789", "runner_status": "running"}
    client.poll_run_status.return_value = {"simulation_id": "test_sim_789", "runner_status": "completed"}
    client.generate_report.return_value = {"simulation_id": "test_sim_789", "status": "generating", "task_id": "task_report"}
    client.poll_generate_report.return_value = {"simulation_id": "test_sim_789", "status": "completed"}
    reports_payload = {
        "reports": [
            {
                "report_id": "test_report_789",
                "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
                "report_text": "Commodity export prices surge",
                "scenarios": [
                    {
                        "scenario_type": "BULL",
                        "trigger": "Commodity rally",
                        "actors": ["BCB"],
                        "confidence": 0.75,
                        "report_excerpt": "Commodity export prices surge",
                    }
                ],
            }
        ]
    }
    client.list_reports.return_value = reports_payload
    client.poll_project_ontology.return_value = {"status": "ONTOLOGY_GENERATED"}
    client.poll_simulation.return_value = {"status": "created"}
    client.poll_report.return_value = reports_payload
    return client


def test_canonical_content_hashing_determinism() -> None:
    payload = {
        "as_of_timestamp": "2026-07-24T00:00:00Z",
        "prompt_template_version": "5A.2-mirofish-seed-v2",
    }
    h1 = ScenarioSeedPackage.compute_seed_id(payload)
    h2 = ScenarioSeedPackage.compute_seed_id(payload)
    assert h1 == h2
    assert len(h1) == 64


def test_blocked_empty_pit_seed(mock_store, mock_client) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=mock_client, store=mock_store)

    seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

    assert run.status == "BLOCKED_EMPTY_PIT_SEED"
    assert run.mirofish_project_id == "BLOCKED_EMPTY_PIT_SEED"
    assert run.mirofish_graph_id == "BLOCKED_EMPTY_PIT_SEED"
    assert run.mirofish_simulation_id == "BLOCKED_EMPTY_PIT_SEED"
    assert len(hyp_list) == 0
    assert sc_set.contradiction_summary == "CONTRADICTION_ANALYSIS_NOT_EXECUTED"


def test_offline_service_fallback(mock_store) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=None, store=mock_store)

    mock_store.get_macro_releases_pit.return_value = [
        {"release_id": "evt_101", "indicator": "IPCA", "actual_value": 0.45, "available_at": cutoff.isoformat()}
    ]
    mock_store.get_evidence_claims_pit.return_value = [
        {"claim_id": "clm_101", "created_at": cutoff.isoformat()}
    ]
    mock_store.get_source_documents_pit.return_value = [
        {"document_id": "doc_101", "available_at": cutoff.isoformat()}
    ]

    seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

    assert run.status == "SERVICE_OFFLINE"
    assert run.mirofish_project_id == "NOT_EXPOSED_BY_SERVICE"
    assert len(hyp_list) == 0
    assert sc_set.contradiction_summary == "CONTRADICTION_ANALYSIS_NOT_EXECUTED"


def test_online_service_success(mock_client, mock_store, tmp_path: Path) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=mock_client, store=mock_store)

    mock_store.get_macro_releases_pit.return_value = [
        {"release_id": "evt_101", "indicator": "IPCA", "actual_value": 0.45, "available_at": cutoff.isoformat()}
    ]
    mock_store.get_evidence_claims_pit.return_value = [
        {"claim_id": "clm_101", "claim_type": "INFLATION", "source_excerpt": "IPCA 0.45%", "created_at": cutoff.isoformat()}
    ]
    mock_store.get_source_documents_pit.return_value = [
        {"document_id": "doc_101", "source_type": "CVM", "available_at": cutoff.isoformat()}
    ]

    with patch("macro_b3_bot.application.mirofish_scenario_engine.Path") as MockPath:
        mock_path_instance = MockPath.return_value
        mock_path_instance.parent.mkdir.return_value = None
        mock_path_instance.__str__.return_value = str(tmp_path / "test_seed.md")
        with patch("builtins.open", MagicMock()):
            seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

            assert run.status == "SUCCESS"
            assert run.mirofish_project_id == "test_proj_123"
            assert run.mirofish_graph_id == "test_graph_456"
            assert run.mirofish_simulation_id == "test_sim_789"
            assert run.raw_report_ids == ["test_report_789"]
            assert len(hyp_list) == 1
            assert hyp_list[0].scenario_type == "BULL"
            assert hyp_list[0].confidence == 0.75
            assert hyp_list[0].verification_status == "UNVERIFIED"
            assert hyp_list[0].report_excerpt == "Commodity export prices surge"
            assert sc_set.contradiction_summary == "CONTRADICTION_ANALYSIS_NOT_EXECUTED"

            mock_client.healthcheck.assert_called_once()
            mock_client.generate_ontology.assert_called_once()
            config = mock_client.create_simulation.call_args.kwargs["config"]
            assert config["report_schema_version"] == MIROFISH_REPORT_SCHEMA_VERSION
            assert config["require_structured_report"] is True


def test_online_service_incomplete_ontology_fail(mock_client, mock_store, tmp_path: Path) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=mock_client, store=mock_store)

    mock_store.get_macro_releases_pit.return_value = [
        {"release_id": "evt_101", "indicator": "IPCA", "actual_value": 0.45, "available_at": cutoff.isoformat()}
    ]
    mock_store.get_evidence_claims_pit.return_value = [
        {"claim_id": "clm_101", "created_at": cutoff.isoformat()}
    ]
    mock_store.get_source_documents_pit.return_value = [
        {"document_id": "doc_101", "available_at": cutoff.isoformat()}
    ]
    mock_client.generate_ontology.return_value = {"project_id": "test_proj_123"}

    with patch("macro_b3_bot.application.mirofish_scenario_engine.Path") as MockPath:
        mock_path_instance = MockPath.return_value
        mock_path_instance.parent.mkdir.return_value = None
        mock_path_instance.__str__.return_value = str(tmp_path / "test_seed.md")
        with patch("builtins.open", MagicMock()):
            seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

            assert run.status == "FAILED_INCOMPLETE_SERVICE_RUN"
            assert len(hyp_list) == 0


def test_online_service_incomplete_simulation_fail(mock_client, mock_store, tmp_path: Path) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=mock_client, store=mock_store)

    mock_store.get_macro_releases_pit.return_value = [
        {"release_id": "evt_101", "indicator": "IPCA", "actual_value": 0.45, "available_at": cutoff.isoformat()}
    ]
    mock_store.get_evidence_claims_pit.return_value = [
        {"claim_id": "clm_101", "created_at": cutoff.isoformat()}
    ]
    mock_store.get_source_documents_pit.return_value = [
        {"document_id": "doc_101", "available_at": cutoff.isoformat()}
    ]
    mock_client.create_simulation.return_value = {}

    with patch("macro_b3_bot.application.mirofish_scenario_engine.Path") as MockPath:
        mock_path_instance = MockPath.return_value
        mock_path_instance.parent.mkdir.return_value = None
        mock_path_instance.__str__.return_value = str(tmp_path / "test_seed.md")
        with patch("builtins.open", MagicMock()):
            seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

            assert run.status == "FAILED_INCOMPLETE_SERVICE_RUN"
            assert len(hyp_list) == 0


def test_online_service_no_reports_fail(mock_client, mock_store, tmp_path: Path) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=mock_client, store=mock_store)

    mock_store.get_macro_releases_pit.return_value = [
        {"release_id": "evt_101", "indicator": "IPCA", "actual_value": 0.45, "available_at": cutoff.isoformat()}
    ]
    mock_store.get_evidence_claims_pit.return_value = [
        {"claim_id": "clm_101", "created_at": cutoff.isoformat()}
    ]
    mock_store.get_source_documents_pit.return_value = [
        {"document_id": "doc_101", "available_at": cutoff.isoformat()}
    ]
    mock_client.list_reports.return_value = {"reports": []}
    mock_client.poll_report.return_value = {"reports": []}

    with patch("macro_b3_bot.application.mirofish_scenario_engine.Path") as MockPath:
        mock_path_instance = MockPath.return_value
        mock_path_instance.parent.mkdir.return_value = None
        mock_path_instance.__str__.return_value = str(tmp_path / "test_seed.md")
        with patch("builtins.open", MagicMock()):
            seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

            assert run.status == "FAILED_INCOMPLETE_SERVICE_RUN"
            assert len(hyp_list) == 0


def test_altering_report_content_changes_hypothesis_ids() -> None:
    report1 = {
        "report_id": "rep_001",
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "report_text": "Selic interest rate cut expected to boost credit",
        "scenarios": [
            {
                "scenario_type": "BULL",
                "trigger": "Selic cut by 50bps",
                "actors": ["BCB"],
                "confidence": 0.80,
                "report_excerpt": "Selic interest rate cut expected to boost credit",
            }
        ],
    }
    report2 = {
        "report_id": "rep_001",
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "report_text": "Selic interest rate hike expected to contract credit",
        "scenarios": [
            {
                "scenario_type": "BEAR",
                "trigger": "Selic hike by 50bps",
                "actors": ["BCB"],
                "confidence": 0.60,
                "report_excerpt": "Selic interest rate hike expected to contract credit",
            }
        ],
    }
    engine = MiroFishScenarioEngine()
    hyp1 = engine._parse_mirofish_report_to_hypotheses(report1, run_id="run_001")
    hyp2 = engine._parse_mirofish_report_to_hypotheses(report2, run_id="run_001")

    assert len(hyp1) == 1
    assert len(hyp2) == 1
    assert hyp1[0].hypothesis_id != hyp2[0].hypothesis_id
    assert hyp1[0].trigger != hyp2[0].trigger


def test_idempotent_report_re_run() -> None:
    report = {
        "report_id": "rep_001",
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "report_text": "Selic interest rate cut expected to boost credit",
        "scenarios": [
            {
                "scenario_type": "BULL",
                "trigger": "Selic cut by 50bps",
                "actors": ["BCB"],
                "confidence": 0.80,
                "report_excerpt": "Selic interest rate cut expected to boost credit",
            }
        ],
    }
    engine = MiroFishScenarioEngine()
    hyp1 = engine._parse_mirofish_report_to_hypotheses(report, run_id="run_001")
    hyp2 = engine._parse_mirofish_report_to_hypotheses(report, run_id="run_001")

    assert hyp1[0].hypothesis_id == hyp2[0].hypothesis_id


def test_healthcheck_rejects_unhealthy_statuses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/401" in path:
            return httpx.Response(401, json={"detail": "Unauthorized"})
        if "/403" in path:
            return httpx.Response(403, json={"detail": "Forbidden"})
        if "/404" in path:
            return httpx.Response(404, json={"detail": "Not Found"})
        if "/429" in path:
            return httpx.Response(429, json={"detail": "Rate Limit Exceeded"})
        if "/200" in path:
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(500, json={"detail": "Internal Error"})

    transport = httpx.MockTransport(handler)
    client_200 = MiroFishClient("http://localhost:20128", graph_prefix="/200")
    client_200.client = httpx.Client(base_url="http://localhost:20128", transport=transport)
    assert client_200.healthcheck() is True

    for err_code in ("401", "403", "404", "429"):
        cli = MiroFishClient("http://localhost:20128", graph_prefix=f"/{err_code}")
        cli.client = httpx.Client(base_url="http://localhost:20128", transport=transport)
        assert cli.healthcheck() is False


def test_native_report_contract_is_strict_and_versioned() -> None:
    valid = {
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "report_text": "A native report excerpt.",
        "scenarios": [{
            "scenario_type": "BASE",
            "trigger": "A trigger",
            "report_excerpt": "A native report excerpt.",
        }],
    }
    assert MiroFishClient.validate_structured_report(valid) == (True, "VALID")
    invalid = {**valid, "schema_version": "unknown"}
    assert MiroFishClient.validate_structured_report(invalid)[0] is False
    assert MiroFishClient.validate_structured_report({"scenarios": []})[0] is False


def test_null_confidence_parsing() -> None:
    raw_report = {
        "report_id": "rep_999",
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "report_text": "Unanticipated liquidity squeeze",
        "scenarios": [
            {
                "scenario_type": "CONTRARIANS",
                "trigger": "Unanticipated liquidity squeeze",
                "actors": ["CVM"],
                "report_excerpt": "Unanticipated liquidity squeeze",
            }
        ],
    }
    engine = MiroFishScenarioEngine()
    hypotheses = engine._parse_mirofish_report_to_hypotheses(raw_report, run_id="run_test_null")

    assert len(hypotheses) == 1
    hyp = hypotheses[0]
    assert hyp.confidence is None
    assert hyp.scenario_type == "UNKNOWN"  # CONTRARIANS is mapped to UNKNOWN
    assert hyp.verification_status == "UNVERIFIED"


def test_no_scenarios_report_yields_zero_hypotheses_and_unsupported_status(mock_client, mock_store, tmp_path: Path) -> None:
    cutoff = datetime.now(timezone.utc)
    engine = MiroFishScenarioEngine(client=mock_client, store=mock_store)

    mock_store.get_macro_releases_pit.return_value = [
        {"release_id": "evt_101", "indicator": "IPCA", "actual_value": 0.45, "available_at": cutoff.isoformat()}
    ]
    mock_store.get_evidence_claims_pit.return_value = [
        {"claim_id": "clm_101", "created_at": cutoff.isoformat()}
    ]
    mock_store.get_source_documents_pit.return_value = [
        {"document_id": "doc_101", "available_at": cutoff.isoformat()}
    ]

    # Report with narrative text but zero scenarios
    mock_client.list_reports.return_value = {
        "reports": [
            {
                "report_id": "rep_narrative_only",
                "analysis_summary": "Plain narrative summary with no structured scenarios embedded.",
            }
        ]
    }
    mock_client.poll_report.return_value = mock_client.list_reports.return_value

    with patch("macro_b3_bot.application.mirofish_scenario_engine.Path") as MockPath:
        mock_path_instance = MockPath.return_value
        mock_path_instance.parent.mkdir.return_value = None
        mock_path_instance.__str__.return_value = str(tmp_path / "test_seed.md")
        with patch("builtins.open", MagicMock()):
            seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

            assert len(hyp_list) == 0
            assert run.status == "FAILED_UNSUPPORTED_REPORT_SCHEMA"
            assert "UNSUPPORTED_REPORT_SCHEMA" in sc_set.missing_variables


def test_no_old_template_strings_in_production_code() -> None:
    engine_file = Path("src/macro_b3_bot/application/mirofish_scenario_engine.py")
    content = engine_file.read_text(encoding="utf-8")

    assert "MiroFish Point-in-Time macro simulation baseline reaction" not in content
    assert "Favorable inflation acceleration trajectory and market re-rating" not in content
    assert "DISINFLATION_MOMENTUM" not in content
    assert "RETAIL_SECTOR_ADJUSTMENT" not in content
    assert "confidence\": 0.85" not in content
    assert "confidence\": 0.75" not in content


def test_unverifiable_report_excerpt_raises_error() -> None:
    raw_report = {
        "report_id": "rep_001",
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "report_text": "Official report narrative content.",
        "scenarios": [{
            "scenario_type": "BASE",
            "trigger": "Official report narrative content.",
            "report_excerpt": "Fabricated excerpt string completely absent from raw_report json",
        }],
    }
    engine = MiroFishScenarioEngine()
    with pytest.raises(ValueError, match="report_excerpt .* is not a verifiable substring of raw report"):
        engine._parse_mirofish_report_to_hypotheses(raw_report, run_id="run_001")


def test_malformed_structured_scenario_is_not_promoted_to_hypothesis() -> None:
    engine = MiroFishScenarioEngine()
    raw_report = {
        "report_id": "rep_malformed",
        "schema_version": MIROFISH_REPORT_SCHEMA_VERSION,
        "scenarios": [{"scenario_type": "BASE", "trigger": "x", "report_excerpt": "x", "macro_factors": "not-a-list"}],
    }

    assert engine._parse_mirofish_report_to_hypotheses(
        raw_report, run_id="run_malformed"
    ) == []


def test_raw_report_persisted_to_duckdb(tmp_path: Path) -> None:
    db_path = tmp_path / "test_raw_report.duckdb"
    store = DatabaseStore(db_path)

    record = {
        "report_id": "rep_raw_123",
        "simulation_id": "sim_123",
        "project_id": "proj_123",
        "content_checksum": "abc123sha256",
        "byte_size": 1024,
        "mime_type": "application/json",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_endpoint": "/api/report/list",
        "file_path": "data/raw/mirofish/reports/abc123sha256.json",
        "canonical_payload_json": "{\"test\": 1}",
    }
    store.save_raw_mirofish_report(record)

    row = store.connection.execute(
        "SELECT report_id, simulation_id, content_checksum, byte_size FROM raw_mirofish_reports WHERE report_id = ?",
        ["rep_raw_123"],
    ).fetchone()

    assert row is not None
    assert row[0] == "rep_raw_123"
    assert row[1] == "sim_123"
    assert row[2] == "abc123sha256"
    assert row[3] == 1024

    store.close()


def test_duckdb_hypothesis_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "test_macro.duckdb"
    store = DatabaseStore(db_path)

    hyp_payload = {
        "hypothesis_id": "hyp_test_001",
        "simulation_run_id": "run_001",
        "scenario_type": "BEAR",
        "verification_status": "UNVERIFIED",
        "confidence": None,
        "trigger": "Oil price shock",
    }
    store.save_scenario_hypothesis(hyp_payload)

    row = store.connection.execute(
        "SELECT hypothesis_id, scenario_type, verification_status, confidence FROM scenario_hypotheses WHERE hypothesis_id = ?",
        ["hyp_test_001"],
    ).fetchone()

    assert row is not None
    assert row[0] == "hyp_test_001"
    assert row[1] == "BEAR"
    assert row[2] == "UNVERIFIED"
    assert row[3] is None

    store.close()
