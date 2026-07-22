import sys
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.adapters.b3_screener import B3ScreenerJsonBridge
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.domain.models import OpportunityAssessment, AssetClass
from macro_b3_bot.application.pipeline import DecisionPipeline

class TestB3IngestionAndResearchMode(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.export_file = self.data_dir / "universe.json"
        self.db_file = self.data_dir / "audit.duckdb"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_and_invalid_export_parsing(self):
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "records": [
                {
                    "ticker": "VALE3",
                    "asset_class": "stock",
                    "price": 65.5,
                    "avg_daily_volume_brl": 500000000,
                    "sector": "mining"
                },
                {
                    "ticker": "INVALID_PRICE",
                    "asset_class": "stock",
                    "price": -10.0,
                    "avg_daily_volume_brl": 1000
                },
                {
                    "ticker": "",
                    "asset_class": "stock",
                    "price": 15.0
                }
            ]
        }
        self.export_file.write_text(json.dumps(payload), encoding="utf-8")
        
        bridge = B3ScreenerJsonBridge(self.export_file)
        assets = bridge.load_assets()
        
        # Somente VALE3 e valido (outro tem preco invalido, outro tem ticker ausente)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].ticker, "VALE3")
        self.assertEqual(assets[0].price, 65.5)

    def test_duckdb_persistence(self):
        store = DatabaseStore(self.db_file)
        snapshot = {
            "ticker": "PETR4",
            "asset_class": "stock",
            "as_of": datetime.now(timezone.utc),
            "price": 38.5,
            "avg_daily_volume_brl": 800000000,
            "sector": "oil_gas",
            "metrics": {"pe": 4.5, "dividend_yield": 0.12}
        }
        store.save_asset_snapshot(snapshot)
        self.assertEqual(store.count_snapshots(), 1)
        store.close()

    def test_research_mode_buy_blocking(self):
        settings = Settings(research_mode=True, allow_buy_signals=False)
        pipeline = DecisionPipeline(settings=settings)
        
        high_score_assessment = OpportunityAssessment(
            ticker="VALE3",
            asset_class=AssetClass.STOCK,
            event_id="EVT_TEST",
            evidence_quality=1.0,
            scenario_probability=1.0,
            causal_strength=1.0,
            company_exposure=1.0,
            fundamental_quality=1.0,
            valuation_attractiveness=1.0,
            entry_timing=1.0,
            portfolio_fit=1.0,
            confidence=0.90,
            expected_upside=0.30,
            expected_downside=-0.10,
            independent_evidence_count=5,
            has_primary_source=True,
            risk_veto=False,
            skeptic_veto=False
        )
        
        decisions = pipeline.evaluate([high_score_assessment])
        self.assertEqual(len(decisions), 1)
        # Deve ter sido convertido de BUY para WATCH e max_position = 0.0
        self.assertEqual(decisions[0].action.value, "watch")
        self.assertEqual(decisions[0].max_position_pct, 0.0)
        self.assertIn("BUY blocked: research mode", decisions[0].reasons)

if __name__ == "__main__":
    unittest.main()
