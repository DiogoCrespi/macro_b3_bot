"""Sprint 4B.1 causal-semantics and point-in-time tests."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from macro_b3_bot.application.evaluate_sector_impacts import CausalGraphEngine, causal_root
from macro_b3_bot.domain.causal_models import CausalEdge
from macro_b3_bot.infrastructure.store import DatabaseStore


NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)


def event(event_id: str, event_type: str, direction: str, age_days: int = 90,
          status: str = "MACRO_EVENT_APPROVED") -> dict:
    available = NOW - timedelta(days=age_days)
    return {
        "event_id": event_id, "event_type": event_type, "direction": direction,
        "indicator": "test", "detected_at": available, "event_available_at": available,
        "surprise_score": 0.9, "novelty_score": 0.8, "persistence_score": 0.7,
        "data_quality_score": 0.95, "status": status,
        "score_breakdown": '{"effective_surprise": 0.8}', "current_regime": "MIXED",
        "ingestion_run_id": "macro_run",
    }


@pytest.fixture
def engine(tmp_path: Path):
    store = DatabaseStore(tmp_path / "causal.duckdb")
    value = CausalGraphEngine(store, "sector_run")
    yield value
    store.close()


@pytest.mark.parametrize(("event_type", "direction", "root"), [
    ("MONETARY_POLICY_SURPRISE", "HAWKISH", "MONETARY_POLICY_HAWKISH"),
    ("MONETARY_POLICY_SURPRISE", "DOVISH", "MONETARY_POLICY_DOVISH"),
    ("GROWTH_SURPRISE", "RISING", "GROWTH_SURPRISE_UP"),
    ("GROWTH_SURPRISE", "FALLING", "GROWTH_SURPRISE_DOWN"),
    ("OIL_PRICE_SHOCK", "BULLISH_OIL", "OIL_PRICE_SHOCK_UP"),
    ("OIL_PRICE_SHOCK", "BEARISH_OIL", "OIL_PRICE_SHOCK_DOWN"),
    ("USD_REGIME_SHIFT", "USD_STRENGTHENING", "USD_REGIME_SHIFT_UP"),
    ("USD_REGIME_SHIFT", "USD_WEAKENING", "USD_REGIME_SHIFT_DOWN"),
    ("ENSO_PHASE_CHANGE", "EL_NINO", "ENSO_PHASE_CHANGE_EL_NINO"),
    ("ENSO_PHASE_CHANGE", "LA_NINA", "ENSO_PHASE_CHANGE_LA_NINA"),
])
def test_causal_root_contract(event_type: str, direction: str, root: str) -> None:
    assert causal_root(event_type, direction) == root


def test_graph_version_edge_count_coverage_and_hypotheses(engine: CausalGraphEngine) -> None:
    assert engine.graph_version == "1.2.0"
    assert len(engine.edges) == 42
    assert engine.validate_event_coverage()["missing"] == []
    assert all(edge.evidence_ids or edge.hypothesis for edge in engine.edges)


def test_explicit_empty_sector_states(engine: CausalGraphEngine) -> None:
    no_signal = engine.sector_state_or_empty("PETROLEO_GAS", NOW, True)
    missing = engine.sector_state_or_empty("PETROLEO_GAS", NOW, False)
    uncovered = engine.sector_state_or_empty("SEM_GRAFO", NOW, True)
    assert no_signal.status == "SECTOR_STATE_NO_ACTIVE_SIGNAL"
    assert missing.status == "SECTOR_STATE_MISSING_DATA"
    assert uncovered.status == "SECTOR_STATE_NO_GRAPH_COVERAGE"
    assert no_signal.net_impact == 0


def test_hypothesis_impact_cannot_be_approved(engine: CausalGraphEngine) -> None:
    candidate = {
        item.sector: item
        for item in engine.propagate_event(
            event("strong", "MONETARY_POLICY_SURPRISE", "HAWKISH", age_days=90), NOW
        )
    }["VAREJO"]
    assert candidate.evidence_status == "HYPOTHESIS"
    assert candidate.status != "SECTOR_IMPACT_APPROVED"


def test_unvalidated_edge_must_be_explicit_hypothesis() -> None:
    with pytest.raises(ValueError, match="marked as a hypothesis"):
        CausalEdge(edge_id="x", source_node="A", target_node="B", direction=1,
                   strength=1, confidence=1, hypothesis=False, rationale="test")


@pytest.mark.parametrize(("event_type", "up", "down", "sector"), [
    ("MONETARY_POLICY_SURPRISE", "HAWKISH", "DOVISH", "VAREJO"),
    ("GROWTH_SURPRISE", "RISING", "FALLING", "LOGISTICA"),
    ("OIL_PRICE_SHOCK", "BULLISH_OIL", "BEARISH_OIL", "PETROLEO_GAS"),
    ("USD_REGIME_SHIFT", "USD_STRENGTHENING", "USD_WEAKENING", "PAPEL_CELULOSE"),
])
def test_opposite_macro_directions_reverse_sector_sign(engine: CausalGraphEngine,
                                                       event_type: str, up: str, down: str,
                                                       sector: str) -> None:
    first = {c.sector: c for c in engine.propagate_event(event("up", event_type, up), NOW)}[sector]
    second = {c.sector: c for c in engine.propagate_event(event("down", event_type, down), NOW)}[sector]
    assert first.impact_score * second.impact_score < 0


def test_effective_surprise_and_watch_weight(engine: CausalGraphEngine) -> None:
    approved = engine._event_strength(event("a", "GROWTH_SURPRISE", "RISING"))
    watch = engine._event_strength(event("w", "GROWTH_SURPRISE", "RISING", status="MACRO_EVENT_WATCH"))
    assert watch == pytest.approx(approved * 0.6)


def temporal_graph(tmp_path: Path) -> Path:
    path = tmp_path / "temporal.yaml"
    path.write_text("""
graph_version: test-time
edges:
  - edge_id: root
    source_node: GROWTH_SURPRISE_UP
    target_node: B3_SECTOR_TEST
    direction: 1
    strength: 1
    confidence: 1
    factor: ECONOMIC_ACTIVITY
    company_channel_effects: {revenue: 1, demand: 1}
    lag_days: 10
    horizon_days: 30
    half_life_days: 10
    hypothesis: true
    rationale: temporal test
""", encoding="utf-8")
    return path


def test_lag_horizon_and_decay(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "time.duckdb")
    engine = CausalGraphEngine(store, "run", temporal_graph(tmp_path))
    assert engine.propagate_event(event("pre", "GROWTH_SURPRISE", "RISING", 9), NOW) == []
    at_lag = engine.propagate_event(event("lag", "GROWTH_SURPRISE", "RISING", 10), NOW)[0]
    decayed = engine.propagate_event(event("decay", "GROWTH_SURPRISE", "RISING", 20), NOW)[0]
    assert decayed.impact_score < at_lag.impact_score
    assert engine.propagate_event(event("expired", "GROWTH_SURPRISE", "RISING", 31), NOW) == []
    store.close()


def test_sector_snapshot_preserves_cross_event_conflict(engine: CausalGraphEngine) -> None:
    bullish = engine.propagate_event(event("up", "GROWTH_SURPRISE", "RISING"), NOW)
    bearish = engine.propagate_event(event("down", "OIL_PRICE_SHOCK", "BULLISH_OIL"), NOW)
    logistics = [c for c in bullish + bearish if c.sector == "LOGISTICA"]
    snapshot = engine.aggregate_sector_state(logistics, NOW)[0]
    assert snapshot.supporting_event_ids == ["up"]
    assert snapshot.opposing_event_ids == ["down"]
    assert snapshot.conflict_ratio > 0


def insert_event(store: DatabaseStore, event_id: str, run_id: str, detected_at: datetime) -> None:
    store.connection.execute("""
        INSERT INTO macro_event_candidates (
          event_id,event_type,indicator,geography,affected_variables,reference_date,detected_at,
          horizon_months,surprise_score,novelty_score,persistence_score,regime_shift_score,
          data_quality_score,direction,current_regime,evidence_ids,status,score_breakdown,ingestion_run_id
        ) VALUES (?, 'GROWTH_SURPRISE','test','[]','[]',?, ?,3,.9,.8,.7,.5,.95,
                  'RISING','MIXED','[]','MACRO_EVENT_APPROVED','{"effective_surprise":.8}',?)
    """, [event_id, detected_at.date(), detected_at, run_id])


def test_run_id_as_of_idempotency_and_replay(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "pit.duckdb")
    insert_event(store, "eligible", "wanted", NOW - timedelta(days=20))
    insert_event(store, "wrong-run", "other", NOW - timedelta(days=20))
    insert_event(store, "future", "wanted", NOW + timedelta(days=1))
    engine = CausalGraphEngine(store, "sector-run", temporal_graph(tmp_path))
    first = engine.evaluate_events_window(date(2025, 1, 1), NOW, "wanted")
    second = engine.evaluate_events_window(date(2025, 1, 1), NOW, "wanted")
    assert first["macro_events_processed"] == 1
    assert first["macro_event_run_id"] == "wanted"
    assert first["sector_run_id"] == "sector-run"
    assert first["active_paths"] == 1
    assert second == first
    assert store.connection.execute("SELECT COUNT(*) FROM sector_impact_candidates").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM sector_state_snapshots").fetchone()[0] == 1
    store.close()


def test_timezone_aware_as_of_compares_against_naive_utc_storage(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "timezone.duckdb")
    detected_naive_utc = datetime(2026, 7, 22, 22, 26, 48)
    insert_event(store, "utc-event", "utc-run", detected_naive_utc)
    engine = CausalGraphEngine(store, "sector-utc", temporal_graph(tmp_path))
    cutoff = datetime.fromisoformat("2026-07-22T23:59:59+00:00")
    summary = engine.evaluate_events_window(date(2025, 1, 1), cutoff, "utc-run")
    assert summary["macro_events_processed"] == 1
    store.close()


def test_no_ticker_or_buy_fields(engine: CausalGraphEngine) -> None:
    for candidate in engine.propagate_event(event("oil", "OIL_PRICE_SHOCK", "BULLISH_OIL"), NOW):
        assert "ticker" not in candidate.model_dump()
        assert "buy" not in candidate.model_dump()
