from datetime import datetime, timezone

from macro_b3_bot.application.run_historical_bridge_validation import (
    HistoricalBridgeValidator,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_p4_missing_series_is_blocked_and_never_promoted(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "p4.duckdb")
    validator = HistoricalBridgeValidator(store, "p4-test")
    result = validator._missing_series(
        "KLBN11", "IPCA_DEBT", datetime(2026, 7, 22, tzinfo=timezone.utc)
    )
    assert result["status"] == "BLOCKED_MISSING_MACRO_SERIES"
    assert result["walk_forward_window_count"] == 0
    assert result["promotion_status"] == "NOT_PROMOTED_TO_VALUATION"
    validator.store.save_historical_bridge_validation(result)
    validator.store.connection.commit()
    row = store.connection.execute(
        "SELECT status, validation_payload FROM historical_bridge_validation_runs WHERE validation_id = ?",
        [result["validation_id"]],
    ).fetchone()
    assert row[0] == "BLOCKED_MISSING_MACRO_SERIES"
    assert "PIT_MACRO_SERIES_NOT_AVAILABLE" in row[1]
    store.close()


def test_p4_result_identity_changes_with_metrics(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "p4-id.duckdb")
    validator = HistoricalBridgeValidator(store, "p4-test")
    as_of = datetime(2026, 7, 22, tzinfo=timezone.utc)
    first = validator._base_result(
        ticker="SUZB3", bridge="FX_OPERATING_REVENUE", as_of=as_of,
        status="EMPIRICAL_OUT_OF_SAMPLE_REVIEW", windows=5,
        in_sample_mae=0.1, out_of_sample_mae=0.2, missing_drivers=[],
        observations=6, notes="test",
    )
    second = validator._base_result(
        ticker="SUZB3", bridge="FX_OPERATING_REVENUE", as_of=as_of,
        status="EMPIRICAL_OUT_OF_SAMPLE_REVIEW", windows=5,
        in_sample_mae=0.1, out_of_sample_mae=0.3, missing_drivers=[],
        observations=6, notes="test",
    )
    assert first["validation_id"] != second["validation_id"]
    store.close()
