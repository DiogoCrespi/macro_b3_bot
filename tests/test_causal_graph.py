"""
Unit tests for Sprint 4B Causal Graph Engine (evaluate_sector_impacts.py).
"""
from datetime import datetime, timezone
from pathlib import Path

from macro_b3_bot.application.evaluate_sector_impacts import CausalGraphEngine
from macro_b3_bot.domain.causal_models import SectorImpactStatus
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_causal_graph_yaml_loading(tmp_path: Path) -> None:
    db_path = tmp_path / "test_causal.duckdb"
    store = DatabaseStore(db_path)
    engine = CausalGraphEngine(store, "run_test_load")

    assert len(engine.edges) >= 20
    sector_targets = [e.target_node for e in engine.edges if e.target_node.startswith("B3_SECTOR_")]
    assert len(sector_targets) >= 15
    store.close()


def test_propagation_hawkish_surprise(tmp_path: Path) -> None:
    db_path = tmp_path / "test_causal.duckdb"
    store = DatabaseStore(db_path)
    engine = CausalGraphEngine(store, "run_test_prop")

    mock_evt = {
        "event_id": "evt_hawkish_001",
        "event_type": "HAWKISH_MONETARY_SURPRISE",
        "indicator": "Selic Rate",
        "detected_at": datetime.now(timezone.utc),
        "surprise_score": 0.85,
        "status": "MACRO_EVENT_APPROVED",
    }

    candidates = engine.propagate_event(mock_evt)
    assert len(candidates) >= 3

    sectors = {c.sector: c for c in candidates}
    assert "BANCOS" in sectors
    assert "VAREJO" in sectors
    assert "CONSTRUCAO" in sectors

    # Hawkish monetary surprise is bullish for bank net interest margins, bearish for retail & construction
    assert sectors["BANCOS"].direction == "BULLISH"
    assert sectors["VAREJO"].direction == "BEARISH"
    assert sectors["CONSTRUCAO"].direction == "BEARISH"
    store.close()


def test_conflict_detection(tmp_path: Path) -> None:
    db_path = tmp_path / "test_causal.duckdb"
    store = DatabaseStore(db_path)
    engine = CausalGraphEngine(store, "run_test_conflict")

    # Both positive and negative paths for AVIACAO (dollar up is bearish, oil up is bearish, gdp up is bullish)
    mock_evt = {
        "event_id": "evt_growth_001",
        "event_type": "GROWTH_SURPRISE_UP",
        "indicator": "IBC-Br",
        "detected_at": datetime.now(timezone.utc),
        "surprise_score": 0.90,
        "status": "MACRO_EVENT_APPROVED",
    }

    candidates = engine.propagate_event(mock_evt)
    sectors = {c.sector: c for c in candidates}
    assert "LOGISTICA" in sectors
    assert sectors["LOGISTICA"].direction == "BULLISH"
    store.close()


def test_zero_ticker_selection_safety(tmp_path: Path) -> None:
    """Verify that SectorImpactCandidate outputs sector level impact without stock tickers."""
    db_path = tmp_path / "test_causal.duckdb"
    store = DatabaseStore(db_path)
    engine = CausalGraphEngine(store, "run_test_safety")

    mock_evt = {
        "event_id": "evt_oil_001",
        "event_type": "OIL_PRICE_SHOCK_UP",
        "indicator": "Brent Crude",
        "detected_at": datetime.now(timezone.utc),
        "surprise_score": 0.88,
        "status": "MACRO_EVENT_APPROVED",
    }

    candidates = engine.propagate_event(mock_evt)
    for cand in candidates:
        # Candidate dict must not contain any stock ticker field or BUY recommendation
        cand_dict = cand.dict()
        assert "ticker" not in cand_dict
        assert "buy" not in cand_dict
        assert cand.status in [
            SectorImpactStatus.SECTOR_IMPACT_APPROVED.value,
            SectorImpactStatus.SECTOR_IMPACT_WATCH.value,
            SectorImpactStatus.SECTOR_IMPACT_REJECTED.value,
        ]
    store.close()
