"""Tests for Sprint 5A MiroFish Scenario Engine Integration."""

from datetime import datetime, timezone
from pathlib import Path
from macro_b3_bot.domain.mirofish_scenario_models import (
    MiroFishSimulationRun,
    ScenarioHypothesis,
    ScenarioSeedPackage,
    ScenarioSet,
)
from macro_b3_bot.application.mirofish_scenario_engine import MiroFishScenarioEngine
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_seed_package_pit_bounding() -> None:
    engine = MiroFishScenarioEngine(client=None)
    cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)

    events = [
        {"event_id": "evt_past_1", "available_at": "2024-01-15T00:00:00Z"},
        {"event_id": "evt_past_2", "available_at": "2024-05-30T00:00:00Z"},
        {"event_id": "evt_future_1", "available_at": "2024-07-01T00:00:00Z"},
    ]

    seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(
        cutoff_dt=cutoff,
        macro_events=events,
    )

    assert "evt_past_1" in seed.material_event_ids
    assert "evt_past_2" in seed.material_event_ids
    assert "evt_future_1" not in seed.material_event_ids


def test_canonical_content_hashing_determinism() -> None:
    payload = {"as_of_timestamp": "2024-01-01T00:00:00Z", "prompt_template_version": "5A.1-mirofish-seed-v1"}
    h1 = ScenarioSeedPackage.compute_seed_id(payload)
    h2 = ScenarioSeedPackage.compute_seed_id(payload)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex string


def test_offline_service_fallback() -> None:
    engine = MiroFishScenarioEngine(client=None)
    cutoff = datetime.now(timezone.utc)

    seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

    assert run.status == "SERVICE_OFFLINE_DETERMINISTIC_FALLBACK"
    assert run.mirofish_project_id == "NOT_EXPOSED_BY_SERVICE"
    assert len(hyp_list) == 5


def test_all_hypotheses_initialized_unverified() -> None:
    engine = MiroFishScenarioEngine(client=None)
    cutoff = datetime.now(timezone.utc)

    seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

    for hyp in hyp_list:
        assert hyp.verification_status == "UNVERIFIED"
        assert hyp.scenario_type in ("BASE", "BULL", "BEAR", "CONTRARIAN", "TAIL")


def test_duckdb_persistence_idempotency(tmp_path: Path) -> None:
    db_path = tmp_path / "test_5a.duckdb"
    store = DatabaseStore(db_path)

    engine = MiroFishScenarioEngine(client=None)
    cutoff = datetime.now(timezone.utc)
    seed, run, sc_set, hyp_list = engine.generate_scenarios_for_cutoff(cutoff_dt=cutoff)

    store.save_scenario_seed_package(seed.model_dump(mode="json"))
    store.save_mirofish_simulation_run(run.model_dump(mode="json"))
    store.save_scenario_set(sc_set.model_dump(mode="json"))

    # Idempotent second save
    store.save_scenario_seed_package(seed.model_dump(mode="json"))
    store.save_mirofish_simulation_run(run.model_dump(mode="json"))
    store.save_scenario_set(sc_set.model_dump(mode="json"))

    row = store.connection.execute("SELECT COUNT(*) FROM scenario_seed_packages").fetchone()
    assert row[0] == 1

    store.close()


def test_no_forbidden_terms_in_models() -> None:
    forbidden_terms = {"buy", "sell_order", "order_submitted", "order_executed", "dcf", "price_target"}

    for model_cls in (ScenarioSeedPackage, MiroFishSimulationRun, ScenarioHypothesis, ScenarioSet):
        for field in model_cls.model_fields.keys():
            for forbidden in forbidden_terms:
                assert forbidden not in field.lower(), f"Forbidden term {forbidden} found in {model_cls.__name__}.{field}"
