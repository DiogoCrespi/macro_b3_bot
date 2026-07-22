import sys
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.adapters.bcb.normalizer import parse_decimal, compute_raw_checksum, split_date_range
from macro_b3_bot.domain.macro_models import MacroObservation, MarketExpectation
from macro_b3_bot.application.macro_surprise import MacroSurpriseDetector

class TestBcbSgsAndNormalizer(unittest.TestCase):

    def test_comma_decimal_parsing(self):
        self.assertEqual(parse_decimal("12,34"), Decimal("12.34"))
        self.assertEqual(parse_decimal("100"), Decimal("100"))
        with self.assertRaises(ValueError):
            parse_decimal("invalid")
        with self.assertRaises(ValueError):
            parse_decimal("NaN")

    def test_checksum_computation(self):
        hash1 = compute_raw_checksum("test payload")
        hash2 = compute_raw_checksum("test payload")
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)

    def test_chunking_over_5_years(self):
        start = date(2010, 1, 1)
        end = date(2025, 12, 31)
        chunks = split_date_range(start, end, max_years=5)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0][0], start)
        self.assertEqual(chunks[-1][1], end)

    def test_macro_surprise_detector(self):
        obs_now = datetime.now(timezone.utc)
        history = [
            MacroObservation(
                source="BCB_SGS", series_code="11", indicator="selic_daily",
                reference_date=date(2026, 1, i), observed_at=obs_now,
                value=Decimal("10.0") + Decimal(i * 0.1), unit="percent",
                frequency="daily", raw_checksum="abc", ingestion_run_id="run1"
            )
            for i in range(1, 6)
        ]
        
        current = MacroObservation(
            source="BCB_SGS", series_code="11", indicator="selic_daily",
            reference_date=date(2026, 1, 6), observed_at=obs_now,
            value=Decimal("11.5"), unit="percent", frequency="daily",
            raw_checksum="xyz", ingestion_run_id="run1"
        )
        
        expectation = MarketExpectation(
            source="BCB_FOCUS", indicator="Selic", reference_date=date(2026, 1, 6),
            target_period="2026", statistic="Mediana", value=Decimal("10.5"),
            observed_at=obs_now, raw_checksum="exp123", ingestion_run_id="run1"
        )

        detector = MacroSurpriseDetector(min_history_window=5)
        surprise = detector.compute_surprise(current, history, expectation)

        self.assertEqual(surprise.indicator, "selic_daily")
        self.assertIsNotNone(surprise.rolling_zscore)
        self.assertGreater(surprise.rolling_zscore, 1.0)
        self.assertEqual(surprise.expectation_error, Decimal("1.0"))

if __name__ == "__main__":
    unittest.main()
