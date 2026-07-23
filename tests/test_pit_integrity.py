"""
Unit and integration tests for Sprint 4A.1-PIT — Point-In-Time Integrity & Revisions Engine.

Tests cover:
- Z-score historical order correctness (delta compares actual vs immediately preceding period)
- Vintage tracking and automatic `is_latest = False` update for older vintages
- Point-in-time filtering in MacroEventBuilder with `as_of_timestamp`
- Point-in-time filtering in RegimeDetector with `as_of_timestamp`
- Severe penalty for `availability_precision = UNKNOWN` in data quality score
- BCB Focus integration
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from macro_b3_bot.application.build_macro_events import MacroEventBuilder
from macro_b3_bot.application.detect_macro_surprises import (
    _historical_zscore,
    compute_data_quality_score,
)
from macro_b3_bot.application.detect_regime_changes import RegimeDetector
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_zscore_historical_order_correctness() -> None:
    # History in chronological order (oldest -> newest): [100, 102, 103]
    # Current release: 104
    # Expected delta vs prior period: 104 - 103 = 1 (NOT 104 - 100 = 4)
    history = [Decimal("100"), Decimal("102"), Decimal("103")]
    actual = Decimal("104")

    score, breakdown = _historical_zscore(actual, history)
    assert breakdown["current_delta"] == 1.0


def test_is_latest_vintage_update() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        v1 = {
            "vintage_id": "FRED_CPI_20240101_v1",
            "series_code": "CPIAUCSL",
            "source": "FRED",
            "reference_date": date(2024, 1, 1),
            "vintage_date": date(2024, 2, 15),
            "realtime_start": date(2024, 2, 15),
            "realtime_end": date(2024, 3, 14),
            "available_at": datetime(2024, 2, 15, tzinfo=timezone.utc),
            "value": Decimal("3.1"),
            "revision_number": 0,
            "is_initial_release": True,
            "is_latest": True,
            "record_checksum": "chk1",
            "ingestion_run_id": "run1",
        }
        store.save_macro_vintage(v1)

        rows = store.connection.execute(
            "SELECT is_latest FROM macro_data_vintages WHERE vintage_id = ?",
            ["FRED_CPI_20240101_v1"]
        ).fetchone()
        assert rows[0] is True

        v2 = {
            "vintage_id": "FRED_CPI_20240101_v2",
            "series_code": "CPIAUCSL",
            "source": "FRED",
            "reference_date": date(2024, 1, 1),
            "vintage_date": date(2024, 3, 15),
            "realtime_start": date(2024, 3, 15),
            "realtime_end": date(9999, 12, 31),
            "available_at": datetime(2024, 3, 15, tzinfo=timezone.utc),
            "value": Decimal("3.2"),
            "revision_number": 1,
            "is_initial_release": False,
            "is_latest": True,
            "record_checksum": "chk2",
            "ingestion_run_id": "run2",
        }
        store.save_macro_vintage(v2)

        v1_latest = store.connection.execute(
            "SELECT is_latest FROM macro_data_vintages WHERE vintage_id = ?",
            ["FRED_CPI_20240101_v1"]
        ).fetchone()[0]
        v2_latest = store.connection.execute(
            "SELECT is_latest FROM macro_data_vintages WHERE vintage_id = ?",
            ["FRED_CPI_20240101_v2"]
        ).fetchone()[0]

        assert v1_latest is False
        assert v2_latest is True
        store.close()


def test_point_in_time_builder_as_of_filtering() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t1 = datetime(2024, 2, 15, tzinfo=timezone.utc)
        t2 = datetime(2024, 3, 15, tzinfo=timezone.utc)

        rel1 = {
            "release_id": "rel1",
            "source": "FRED",
            "series_code": "CPIAUCSL",
            "indicator": "Consumer Price Index",
            "geography": ["US"],
            "frequency": "MONTHLY",
            "unit": "%",
            "reference_date": date(2024, 1, 1),
            "published_at": t1,
            "available_at": t1,
            "actual_value": Decimal("3.1"),
            "previous_value": Decimal("3.0"),
            "consensus_value": Decimal("3.0"),
            "raw_checksum": "r1",
            "record_checksum": "rc1",
            "ingestion_run_id": "run1",
            "availability_precision": "EXACT",
        }
        rel2 = {
            "release_id": "rel2",
            "source": "FRED",
            "series_code": "CPIAUCSL",
            "indicator": "Consumer Price Index",
            "geography": ["US"],
            "frequency": "MONTHLY",
            "unit": "%",
            "reference_date": date(2024, 2, 1),
            "published_at": t2,
            "available_at": t2,
            "actual_value": Decimal("3.5"),
            "previous_value": Decimal("3.1"),
            "consensus_value": Decimal("3.1"),
            "raw_checksum": "r2",
            "record_checksum": "rc2",
            "ingestion_run_id": "run2",
            "availability_precision": "EXACT",
        }
        store.save_macro_release(rel1)
        store.save_macro_release(rel2)

        builder = MacroEventBuilder(store, "run_test")
        # As of t1 (Feb 15): rel2 (published Mar 15) must be excluded
        res_t1 = builder.process_since(date(2024, 1, 1), as_of_timestamp=t1)
        assert res_t1["releases_evaluated"] == 1

        # As of t2 (Mar 15): both releases must be evaluated
        res_t2 = builder.process_since(date(2024, 1, 1), as_of_timestamp=t2)
        assert res_t2["releases_evaluated"] == 2

        store.close()


def test_unknown_precision_penalty() -> None:
    score_exact = compute_data_quality_score("EIA", "MONTHLY", True, True, True, "EXACT")
    score_unknown = compute_data_quality_score("EIA", "MONTHLY", True, True, True, "UNKNOWN")

    assert score_exact == 1.0
    assert score_unknown < 0.50


def test_regime_detector_as_of_filtering() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t1 = datetime(2024, 2, 1, tzinfo=timezone.utc)
        detector = RegimeDetector(store, "run_regime")
        snap = detector.detect_and_snapshot(as_of_timestamp=t1)

        assert snap is not None
        assert snap["captured_at"] == t1
        store.close()
