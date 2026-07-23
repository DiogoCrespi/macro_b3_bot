"""
Unit and integration tests for Sprint 4A.1-PIT2 — Point-In-Time Integrity & Revisions Engine.

Tests cover:
- Z-score historical order correctness (delta compares actual vs immediately preceding period)
- Vintage tracking and automatic `is_latest = False` update for older vintages
- Exact timestamp equality (`available_at == as_of_timestamp`) availability contract
- Point-in-time filtering in MacroEventBuilder with `as_of_timestamp`
- Isolation of historical context in score calculations as of `as_of_timestamp`
- Strict gate lockout for `availability_precision = UNKNOWN` (never MACRO_EVENT_APPROVED)
- Regime detector snapshot_date alignment with `as_of_timestamp.date()`
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


def test_exact_equality_as_of_timestamp() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t_exact = datetime(2024, 3, 15, 14, 30, tzinfo=timezone.utc)

        rel = {
            "release_id": "rel_exact",
            "source": "FRED",
            "series_code": "CPIAUCSL",
            "indicator": "Consumer Price Index",
            "geography": ["US"],
            "frequency": "MONTHLY",
            "unit": "%",
            "reference_date": date(2024, 2, 1),
            "published_at": t_exact,
            "available_at": t_exact,
            "actual_value": Decimal("3.5"),
            "previous_value": Decimal("3.1"),
            "consensus_value": Decimal("3.1"),
            "raw_checksum": "rex",
            "record_checksum": "rcex",
            "ingestion_run_id": "run_ex",
            "availability_precision": "EXACT",
        }
        store.save_macro_release(rel)

        builder = MacroEventBuilder(store, "run_exact")
        # Exact equality available_at == as_of MUST be evaluated (skipped == 0)
        res = builder.process_since(date(2024, 2, 1), as_of_timestamp=t_exact)
        assert res["skipped"] == 0
        assert res["releases_evaluated"] == 1
        store.close()


def test_point_in_time_builder_as_of_filtering() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t1 = datetime(2024, 2, 15, tzinfo=timezone.utc)
        t2 = datetime(2024, 3, 15, tzinfo=timezone.utc)
        t3 = datetime(2024, 3, 16, tzinfo=timezone.utc)

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
        # As of t1 (Feb 15): rel2 (published Mar 15) is skipped due to look-ahead
        res_t1 = builder.process_since(date(2024, 1, 1), as_of_timestamp=t1)
        assert res_t1["skipped"] == 1

        # As of t3 (Mar 16): both releases available, zero skipped
        res_t3 = builder.process_since(date(2024, 1, 1), as_of_timestamp=t3)
        assert res_t3["skipped"] == 0

        store.close()


def test_score_history_as_of_isolation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t1 = datetime(2024, 1, 15, tzinfo=timezone.utc)
        t2 = datetime(2024, 2, 15, tzinfo=timezone.utc)

        # Release 1 available in Jan
        r1 = {
            "release_id": "r1", "source": "FRED", "series_code": "DFF",
            "indicator": "Fed Funds", "geography": ["US"], "frequency": "DAILY",
            "unit": "%", "reference_date": date(2024, 1, 1),
            "published_at": t1, "available_at": t1,
            "actual_value": Decimal("5.25"), "previous_value": Decimal("5.0"),
            "consensus_value": None, "raw_checksum": "ck1", "record_checksum": "rck1",
            "ingestion_run_id": "run1", "availability_precision": "EXACT"
        }
        # Release 2 available in Feb
        r2 = {
            "release_id": "r2", "source": "FRED", "series_code": "DFF",
            "indicator": "Fed Funds", "geography": ["US"], "frequency": "DAILY",
            "unit": "%", "reference_date": date(2024, 2, 1),
            "published_at": t2, "available_at": t2,
            "actual_value": Decimal("5.50"), "previous_value": Decimal("5.25"),
            "consensus_value": None, "raw_checksum": "ck2", "record_checksum": "rck2",
            "ingestion_run_id": "run2", "availability_precision": "EXACT"
        }
        store.save_macro_release(r1)
        store.save_macro_release(r2)

        # Historical query as of t1 must NOT see r2
        releases_t1 = store.get_macro_releases_for_series("FRED", "DFF", limit=10, as_of_timestamp=t1)
        assert len(releases_t1) == 1
        assert releases_t1[0]["release_id"] == "r1"

        # Historical query as of t2 sees both
        releases_t2 = store.get_macro_releases_for_series("FRED", "DFF", limit=10, as_of_timestamp=t2)
        assert len(releases_t2) == 2

        store.close()


def test_unknown_precision_gate_lockout() -> None:
    builder = MacroEventBuilder.__new__(MacroEventBuilder)

    gate_rules = {
        "min_surprise_score": 0.50,
        "min_regime_shift_score": 0.50,
        "min_novelty_score": 0.50,
        "min_data_quality_score": 0.40,
        "watch_min_surprise_score": 0.30,
    }
    # High scores that would qualify for MACRO_EVENT_APPROVED
    status_exact = builder._apply_gate(0.9, 0.9, 0.9, 0.9, 0.9, gate_rules, availability_precision="EXACT")
    status_unknown = builder._apply_gate(0.9, 0.9, 0.9, 0.9, 0.9, gate_rules, availability_precision="UNKNOWN")

    assert status_exact == "MACRO_EVENT_APPROVED"
    assert status_unknown == "MACRO_EVENT_WATCH"  # Lockout forces WATCH for UNKNOWN


def test_unknown_precision_penalty() -> None:
    score_exact = compute_data_quality_score("EIA", "MONTHLY", True, True, True, "EXACT")
    score_unknown = compute_data_quality_score("EIA", "MONTHLY", True, True, True, "UNKNOWN")

    assert score_exact == 1.0
    assert score_unknown < 0.50


def test_regime_detector_as_of_filtering() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t1 = datetime(2024, 2, 1, 12, 0, tzinfo=timezone.utc)
        detector = RegimeDetector(store, "run_regime")
        snap = detector.detect_and_snapshot(as_of_timestamp=t1)

        assert snap is not None
        assert snap["captured_at"] == t1
        assert snap["snapshot_date"] == t1.date()
        store.close()


def test_multi_vintage_reference_date_deduplication() -> None:
    """3 vintages of January (3.0 initial, 3.1 rev1, 3.2 rev2) must collapse into 1 value (3.2)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        t1 = datetime(2024, 2, 15, tzinfo=timezone.utc)
        t2 = datetime(2024, 3, 15, tzinfo=timezone.utc)
        t3 = datetime(2024, 4, 15, tzinfo=timezone.utc)

        # Initial release
        rel_v1 = {
            "release_id": "r_jan_v1", "source": "FRED", "series_code": "CPI",
            "indicator": "CPI", "geography": ["US"], "frequency": "MONTHLY", "unit": "%",
            "reference_date": date(2024, 1, 1), "published_at": t1, "available_at": t1,
            "actual_value": Decimal("3.0"), "previous_value": None, "consensus_value": None,
            "raw_checksum": "chk_v1", "record_checksum": "rck_v1", "ingestion_run_id": "run1",
            "availability_precision": "EXACT_DATE",
        }
        # Revision 1
        rel_v2 = {
            "release_id": "r_jan_v2", "source": "FRED", "series_code": "CPI",
            "indicator": "CPI", "geography": ["US"], "frequency": "MONTHLY", "unit": "%",
            "reference_date": date(2024, 1, 1), "published_at": t2, "available_at": t2,
            "actual_value": Decimal("3.1"), "previous_value": None, "consensus_value": None,
            "raw_checksum": "chk_v2", "record_checksum": "rck_v2", "ingestion_run_id": "run2",
            "availability_precision": "EXACT_DATE",
        }
        # Revision 2
        rel_v3 = {
            "release_id": "r_jan_v3", "source": "FRED", "series_code": "CPI",
            "indicator": "CPI", "geography": ["US"], "frequency": "MONTHLY", "unit": "%",
            "reference_date": date(2024, 1, 1), "published_at": t3, "available_at": t3,
            "actual_value": Decimal("3.2"), "previous_value": None, "consensus_value": None,
            "raw_checksum": "chk_v3", "record_checksum": "rck_v3", "ingestion_run_id": "run3",
            "availability_precision": "EXACT_DATE",
        }
        store.save_macro_release(rel_v1)
        store.save_macro_release(rel_v2)
        store.save_macro_release(rel_v3)

        # As of t3: history query MUST return exactly 1 observation for Jan 2024 with actual_value 3.2
        history = store.get_macro_releases_for_series("FRED", "CPI", limit=10, as_of_timestamp=t3)
        assert len(history) == 1
        assert Decimal(str(history[0]["actual_value"])) == Decimal("3.2")
        store.close()


def test_out_of_order_vintage_insertion() -> None:
    """Inserting rev2 first, then initial, then rev1 must keep rev2 as is_latest=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        # Rev 2 (March) inserted FIRST
        v_rev2 = {
            "vintage_id": "v_rev2", "series_code": "CPI", "source": "FRED",
            "reference_date": date(2024, 1, 1), "vintage_date": date(2024, 3, 15),
            "available_at": datetime(2024, 3, 15, tzinfo=timezone.utc),
            "value": Decimal("3.2"), "revision_number": 2, "is_initial_release": False,
            "is_latest": True, "record_checksum": "c3", "ingestion_run_id": "run3",
        }
        store.save_macro_vintage(v_rev2)

        # Initial (Jan) inserted SECOND (out of order backfill)
        v_initial = {
            "vintage_id": "v_init", "series_code": "CPI", "source": "FRED",
            "reference_date": date(2024, 1, 1), "vintage_date": date(2024, 1, 15),
            "available_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
            "value": Decimal("3.0"), "revision_number": 0, "is_initial_release": True,
            "is_latest": True, "record_checksum": "c1", "ingestion_run_id": "run1",
        }
        store.save_macro_vintage(v_initial)

        # Rev 1 (Feb) inserted THIRD
        v_rev1 = {
            "vintage_id": "v_rev1", "series_code": "CPI", "source": "FRED",
            "reference_date": date(2024, 1, 1), "vintage_date": date(2024, 2, 15),
            "available_at": datetime(2024, 2, 15, tzinfo=timezone.utc),
            "value": Decimal("3.1"), "revision_number": 1, "is_initial_release": False,
            "is_latest": True, "record_checksum": "c2", "ingestion_run_id": "run2",
        }
        store.save_macro_vintage(v_rev1)

        # Check DB state: v_rev2 MUST be the only is_latest = True
        latest_rows = store.connection.execute(
            "SELECT vintage_id FROM macro_data_vintages WHERE source = 'FRED' AND series_code = 'CPI' AND is_latest = True"
        ).fetchall()
        assert len(latest_rows) == 1
        assert latest_rows[0][0] == "v_rev2"
        store.close()
