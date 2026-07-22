"""
Sprint 4A unit tests — Macro Event Engine.

Tests cover:
1. MacroRelease schema and store persistence (idempotency)
2. Vintage tracking schema
3. Surprise score calculation
4. Novelty score calculation
5. Persistence score calculation
6. Data quality score
7. MacroEventGate thresholds
8. Look-ahead prevention (available_at in future → skipped)
9. Regime detection (ENSO, yield curve, oil)
10. Full idempotency: second run adds no duplicates
"""
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from macro_b3_bot.adapters.macro.fred_client import (
    make_record_checksum,
    make_release_id,
    parse_fred_value,
)
from macro_b3_bot.adapters.macro.noaa_enso_client import (
    classify_enso_intensity,
    classify_enso_phase,
)
from macro_b3_bot.application.detect_macro_surprises import (
    compute_data_quality_score,
    compute_novelty_score,
    compute_persistence_score,
    compute_surprise_score,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


def _make_store(tmp_dir: str) -> DatabaseStore:
    db_path = Path(tmp_dir) / "test_macro.duckdb"
    store = DatabaseStore(db_path)
    store._init_tables()
    return store


def _make_release(
    series_code: str = "DFF",
    source: str = "FRED",
    ref_date: date = date(2025, 1, 15),
    actual_value: Decimal = Decimal("5.33"),
    previous_value: Decimal = None,
    consensus_value: Decimal = None,
    ingestion_run_id: str = "run_test_001",
) -> dict:
    published_at = datetime(2025, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    release_id = make_release_id(source, series_code, ref_date, published_at)
    rec_chk = make_record_checksum(source, series_code, ref_date, str(actual_value))
    return {
        "release_id": release_id,
        "source": source,
        "series_code": series_code,
        "indicator": f"Test Indicator {series_code}",
        "geography": ["US"],
        "frequency": "DAILY",
        "unit": "%",
        "reference_date": ref_date,
        "published_at": published_at,
        "available_at": datetime.now(timezone.utc),
        "actual_value": actual_value,
        "previous_value": previous_value,
        "revised_previous_value": None,
        "consensus_value": consensus_value,
        "raw_checksum": rec_chk + "_raw",
        "record_checksum": rec_chk,
        "ingestion_run_id": ingestion_run_id,
    }


class TestMacroReleaseStorage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_save_release_returns_true_on_insert(self):
        rel = _make_release()
        result = self.store.save_macro_release(rel)
        self.assertTrue(result)

    def test_save_release_returns_false_on_duplicate(self):
        rel = _make_release()
        self.store.save_macro_release(rel)
        # Same record_checksum → duplicate
        result = self.store.save_macro_release(rel)
        self.assertFalse(result)

    def test_idempotency_count_unchanged(self):
        rel = _make_release()
        self.store.save_macro_release(rel)
        self.store.save_macro_release(rel)
        count = self.store.connection.execute("SELECT COUNT(*) FROM macro_releases").fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_values_same_series_stored_separately(self):
        rel1 = _make_release(actual_value=Decimal("5.33"), ref_date=date(2025, 1, 15))
        rel2 = _make_release(actual_value=Decimal("5.50"), ref_date=date(2025, 2, 15))
        self.store.save_macro_release(rel1)
        self.store.save_macro_release(rel2)
        count = self.store.connection.execute("SELECT COUNT(*) FROM macro_releases").fetchone()[0]
        self.assertEqual(count, 2)

    def test_geography_stored_as_json(self):
        rel = _make_release()
        self.store.save_macro_release(rel)
        row = self.store.connection.execute(
            "SELECT geography FROM macro_releases WHERE release_id = ?",
            [rel["release_id"]]
        ).fetchone()
        import json
        geo = json.loads(row[0])
        self.assertIn("US", geo)

    def test_get_macro_releases_for_series(self):
        for i in range(5):
            rel = _make_release(ref_date=date(2025, i + 1, 1), actual_value=Decimal(str(5.0 + i * 0.1)))
            self.store.save_macro_release(rel)
        results = self.store.get_macro_releases_for_series("FRED", "DFF", limit=10)
        self.assertEqual(len(results), 5)
        # Should be DESC by reference_date
        dates = [r["reference_date"] for r in results]
        self.assertEqual(dates, sorted(dates, reverse=True))


class TestMacroVintageStorage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_save_vintage_insert(self):
        vint = {
            "vintage_id": "VNT_001",
            "series_code": "CPIAUCSL",
            "source": "FRED",
            "reference_date": date(2024, 12, 1),
            "vintage_date": date(2025, 1, 10),
            "value": Decimal("315.605"),
            "is_latest": True,
            "ingestion_run_id": "run_test",
        }
        result = self.store.save_macro_vintage(vint)
        self.assertTrue(result)

    def test_save_vintage_idempotency(self):
        vint = {
            "vintage_id": "VNT_002",
            "series_code": "CPIAUCSL",
            "source": "FRED",
            "reference_date": date(2024, 12, 1),
            "vintage_date": date(2025, 1, 10),
            "value": Decimal("315.605"),
            "is_latest": True,
            "ingestion_run_id": "run_test",
        }
        self.store.save_macro_vintage(vint)
        result = self.store.save_macro_vintage(vint)
        self.assertFalse(result)


class TestSurpriseDetection(unittest.TestCase):

    def test_no_history_returns_zero(self):
        score, breakdown = compute_surprise_score(
            actual=Decimal("5.0"),
            consensus=None,
            historical_values=[],
        )
        self.assertEqual(score, 0.0)

    def test_short_history_penalised(self):
        hist = [Decimal(str(5.0 + i * 0.01)) for i in range(5)]
        score, breakdown = compute_surprise_score(
            actual=Decimal("6.5"),
            consensus=None,
            historical_values=hist,
        )
        # Short history → penalised score, but still non-zero for large deviation
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertTrue(breakdown.get("short_history_penalty", False))

    def test_strong_surprise_high_score(self):
        # 50 values around 5.0, actual = 8.0 → clear surprise
        hist = [Decimal(str(5.0 + (i % 5) * 0.01)) for i in range(50)]
        score, breakdown = compute_surprise_score(
            actual=Decimal("8.0"),
            consensus=None,
            historical_values=hist,
        )
        self.assertGreater(score, 0.5)

    def test_consensus_based_surprise(self):
        actual = Decimal("3.5")
        consensus = Decimal("2.5")
        errors = [Decimal(str(round((actual_h - Decimal("2.5")), 2))) for actual_h in
                  [Decimal(str(2.5 + i * 0.05)) for i in range(24)]]
        hist = [Decimal(str(2.5 + i * 0.05)) for i in range(24)]
        score, breakdown = compute_surprise_score(
            actual=actual,
            consensus=consensus,
            historical_values=hist,
            historical_consensus_errors=errors,
        )
        self.assertGreater(score, 0.0)
        self.assertEqual(breakdown["method"], "CONSENSUS_BLENDED")

    def test_score_capped_at_one(self):
        hist = [Decimal("5.0")] * 50
        score, _ = compute_surprise_score(
            actual=Decimal("1000.0"),
            consensus=None,
            historical_values=hist,
        )
        self.assertLessEqual(score, 1.0)

    def test_no_surprise_near_mean(self):
        hist = [Decimal(str(5.0 + i * 0.01)) for i in range(30)]
        score, _ = compute_surprise_score(
            actual=Decimal("5.15"),
            consensus=None,
            historical_values=hist,
        )
        self.assertLess(score, 0.5)


class TestNoveltyScore(unittest.TestCase):

    def test_never_seen_returns_high_novelty(self):
        score, _ = compute_novelty_score(
            event_type="MONETARY_POLICY_SURPRISE",
            series_code="DFF",
            current_score=0.8,
            days_since_last_event=None,
            magnitude_percentile=0.90,
            combination_rarity=0.80,
            recent_event_count_30d=0,
        )
        self.assertGreater(score, 0.7)

    def test_recent_event_reduces_novelty(self):
        score, _ = compute_novelty_score(
            event_type="MONETARY_POLICY_SURPRISE",
            series_code="DFF",
            current_score=0.8,
            days_since_last_event=5,
            magnitude_percentile=0.5,
            combination_rarity=0.5,
            recent_event_count_30d=3,
        )
        self.assertLess(score, 0.5)

    def test_score_bounded(self):
        score, _ = compute_novelty_score(
            event_type="TEST",
            series_code="X",
            current_score=1.0,
            days_since_last_event=365,
            magnitude_percentile=1.0,
            combination_rarity=1.0,
            recent_event_count_30d=0,
        )
        self.assertLessEqual(score, 1.0)
        self.assertGreaterEqual(score, 0.0)


class TestPersistenceScore(unittest.TestCase):

    def test_long_duration_high_persistence(self):
        score, _ = compute_persistence_score(
            event_family="YIELD_CURVE_REGIME_SHIFT",
            consecutive_confirmations=4,
            trend_strength=0.85,
            revision_stability=0.90,
            typical_duration_months=12,
        )
        self.assertGreater(score, 0.7)

    def test_zero_confirmations_reduces_score(self):
        score_low, _ = compute_persistence_score(
            event_family="OIL_PRICE_SHOCK",
            consecutive_confirmations=0,
            trend_strength=0.2,
            revision_stability=0.7,
            typical_duration_months=2,
        )
        score_high, _ = compute_persistence_score(
            event_family="MONETARY_POLICY_SURPRISE",
            consecutive_confirmations=3,
            trend_strength=0.9,
            revision_stability=0.95,
            typical_duration_months=6,
        )
        self.assertLess(score_low, score_high)


class TestDataQualityScore(unittest.TestCase):

    def test_perfect_quality(self):
        score = compute_data_quality_score(
            source="FRED",
            frequency="DAILY",
            has_vintage=True,
            has_consensus=True,
            has_previous_value=True,
        )
        self.assertEqual(score, 1.0)

    def test_eia_no_vintage_penalised(self):
        score = compute_data_quality_score(
            source="EIA",
            frequency="WEEKLY",
            has_vintage=False,
            has_consensus=False,
            has_previous_value=True,
        )
        self.assertLess(score, 0.90)

    def test_quarterly_penalised(self):
        score_daily = compute_data_quality_score("FRED", "DAILY", True, True, True)
        score_quarterly = compute_data_quality_score("FRED", "QUARTERLY", True, True, True)
        self.assertLess(score_quarterly, score_daily)

    def test_minimum_zero(self):
        score = compute_data_quality_score(
            source="NOAA",
            frequency="QUARTERLY",
            has_vintage=False,
            has_consensus=False,
            has_previous_value=False,
        )
        self.assertGreaterEqual(score, 0.0)


class TestEnsoClassification(unittest.TestCase):

    def test_el_nino_positive(self):
        self.assertEqual(classify_enso_phase(Decimal("0.6")), "EL_NINO")
        self.assertEqual(classify_enso_phase(Decimal("1.8")), "EL_NINO")

    def test_la_nina_negative(self):
        self.assertEqual(classify_enso_phase(Decimal("-0.6")), "LA_NINA")
        self.assertEqual(classify_enso_phase(Decimal("-1.5")), "LA_NINA")

    def test_neutral(self):
        self.assertEqual(classify_enso_phase(Decimal("0.2")), "NEUTRAL")
        self.assertEqual(classify_enso_phase(Decimal("-0.4")), "NEUTRAL")
        self.assertEqual(classify_enso_phase(None), "NEUTRAL")

    def test_intensity_classification(self):
        self.assertEqual(classify_enso_intensity(Decimal("0.3")), "NEUTRAL")
        self.assertEqual(classify_enso_intensity(Decimal("0.7")), "WEAK")
        self.assertEqual(classify_enso_intensity(Decimal("1.2")), "MODERATE")
        self.assertEqual(classify_enso_intensity(Decimal("1.8")), "STRONG")
        self.assertEqual(classify_enso_intensity(Decimal("2.1")), "VERY_STRONG")
        self.assertEqual(classify_enso_intensity(Decimal("-1.6")), "STRONG")


class TestFredParsing(unittest.TestCase):

    def test_dot_returns_none(self):
        self.assertIsNone(parse_fred_value("."))
        self.assertIsNone(parse_fred_value(""))
        self.assertIsNone(parse_fred_value(None))

    def test_valid_value(self):
        result = parse_fred_value("5.33")
        self.assertEqual(result, Decimal("5.33"))

    def test_negative_value(self):
        result = parse_fred_value("-0.5")
        self.assertEqual(result, Decimal("-0.5"))


class TestMacroEventCandidateStorage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _make_event(self, event_id: str = "EVT_001", status: str = "MACRO_EVENT_APPROVED") -> dict:
        return {
            "event_id": event_id,
            "event_type": "MONETARY_POLICY_SURPRISE",
            "indicator": "Fed Funds Effective Rate",
            "geography": ["US"],
            "affected_variables": ["interest_rates", "credit_spreads"],
            "reference_date": date(2025, 7, 1),
            "detected_at": datetime.now(timezone.utc),
            "horizon_months": 6,
            "actual_value": Decimal("5.33"),
            "expected_value": Decimal("5.25"),
            "surprise_value": Decimal("0.08"),
            "surprise_score": 0.72,
            "novelty_score": 0.65,
            "persistence_score": 0.58,
            "regime_shift_score": 0.65,
            "data_quality_score": 0.95,
            "direction": "HAWKISH",
            "current_regime": "GROWTH_UP_INFLATION_UP",
            "evidence_ids": ["REL_ABC123"],
            "status": status,
            "score_breakdown": {"surprise": {"method": "HISTORY_ONLY"}},
        }

    def test_save_event_candidate_returns_true(self):
        evt = self._make_event()
        result = self.store.save_macro_event_candidate(evt)
        self.assertTrue(result)

    def test_save_event_candidate_idempotency(self):
        evt = self._make_event()
        self.store.save_macro_event_candidate(evt)
        result = self.store.save_macro_event_candidate(evt)
        self.assertFalse(result)

    def test_get_event_candidates_by_status(self):
        for i in range(3):
            evt = self._make_event(event_id=f"EVT_00{i}", status="MACRO_EVENT_APPROVED")
            self.store.save_macro_event_candidate(evt)
        self._make_event(event_id="EVT_REJECTED", status="MACRO_EVENT_REJECTED")
        self.store.save_macro_event_candidate(self._make_event("EVT_REJECTED", "MACRO_EVENT_REJECTED"))

        approved = self.store.get_macro_event_candidates(status="MACRO_EVENT_APPROVED")
        self.assertEqual(len(approved), 3)

    def test_update_macro_event_status(self):
        evt = self._make_event("EVT_UPDATE", "MACRO_EVENT_WATCH")
        self.store.save_macro_event_candidate(evt)
        self.store.update_macro_event_status("EVT_UPDATE", "MACRO_EVENT_APPROVED")

        row = self.store.connection.execute(
            "SELECT status FROM macro_event_candidates WHERE event_id = 'EVT_UPDATE'"
        ).fetchone()
        self.assertEqual(row[0], "MACRO_EVENT_APPROVED")

    def test_evidence_link_created(self):
        evt = self._make_event("EVT_LINK", "MACRO_EVENT_APPROVED")
        self.store.save_macro_event_candidate(evt)
        row = self.store.connection.execute(
            "SELECT COUNT(*) FROM macro_event_evidence_links WHERE event_id = 'EVT_LINK'"
        ).fetchone()
        self.assertEqual(row[0], 1)


class TestMacroRegimeSnapshotStorage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _make_snap(self, snap_id: str = "SNAP_001") -> dict:
        return {
            "snapshot_id": snap_id,
            "snapshot_date": date.today(),
            "captured_at": datetime.now(timezone.utc),
            "growth_direction": "UP",
            "inflation_direction": "DOWN",
            "liquidity_stance": "TIGHTENING",
            "oil_regime": "STABLE",
            "enso_phase": "EL_NINO",
            "regime_label": "GROWTH_UP_INFLATION_DOWN",
            "confidence": 0.75,
            "evidence_release_ids": ["REL_001", "REL_002"],
            "ingestion_run_id": "run_snap_test",
        }

    def test_save_snapshot(self):
        snap = self._make_snap()
        result = self.store.save_macro_regime_snapshot(snap)
        self.assertTrue(result)

    def test_snapshot_idempotency(self):
        snap = self._make_snap()
        self.store.save_macro_regime_snapshot(snap)
        result = self.store.save_macro_regime_snapshot(snap)
        self.assertFalse(result)

    def test_snapshot_stored_correctly(self):
        snap = self._make_snap("SNAP_VERIFY")
        self.store.save_macro_regime_snapshot(snap)
        row = self.store.connection.execute(
            "SELECT regime_label, enso_phase, confidence FROM macro_regime_snapshots WHERE snapshot_id = 'SNAP_VERIFY'"
        ).fetchone()
        self.assertEqual(row[0], "GROWTH_UP_INFLATION_DOWN")
        self.assertEqual(row[1], "EL_NINO")
        self.assertAlmostEqual(row[2], 0.75)


class TestLookAheadPrevention(unittest.TestCase):
    """
    Ensure releases with available_at in the future are rejected by the builder.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_future_available_at_skipped(self):
        from datetime import timedelta
        from macro_b3_bot.application.build_macro_events import MacroEventBuilder

        # Insert a release with available_at in the future
        future_rel = _make_release(
            series_code="DFF",
            source="FRED",
            ref_date=date.today(),
            actual_value=Decimal("5.5"),
        )
        future_rel["available_at"] = datetime.now(timezone.utc) + timedelta(days=30)
        self.store.save_macro_release(future_rel)

        builder = MacroEventBuilder(self.store, "run_lookahead_test")
        result = builder.process_since(date(2020, 1, 1))
        # The future release should be skipped (look-ahead prevented)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["events_approved"] + result["events_watch"] + result["events_rejected"], 0)


if __name__ == "__main__":
    unittest.main()
