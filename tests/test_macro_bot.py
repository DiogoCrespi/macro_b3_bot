import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.data_ingestion.b3_screener_bridge import B3ScreenerBridge
from core.mirofish_engine.swarm_simulation import SwarmSimulationEngine
from core.execution.portfolio_allocator import PortfolioAllocator

class TestMacroB3Bot(unittest.TestCase):

    def test_b3_screener_bridge(self):
        bridge = B3ScreenerBridge()
        data = bridge.load_data()
        self.assertIn("stocks", data)
        self.assertIn("fiis", data)
        self.assertGreater(len(bridge.get_stocks()), 0)

    def test_swarm_simulation(self):
        engine = SwarmSimulationEngine()
        macro_snapshot = {
            "dxy": {"change_pct": 0.001},
            "sp500": {"change_pct": 0.002},
            "gold": {"change_pct": 0.01},
            "oil": {"change_pct": 0.04}
        }
        news = ["El Nino traz seca para os reservatorios hidricos e eleva commodities"]
        sim = engine.run_simulation(macro_snapshot, news)
        self.assertIn("SUPER_CICLO_COMMODITIES", sim["active_drivers"])
        self.assertIn("EL_NINO_SECAGEM", sim["active_drivers"])
        self.assertTrue(len(sim["favored_tickers"]) > 0)

    def test_portfolio_allocator(self):
        allocator = PortfolioAllocator()
        approved = [
            {"ticker": "VALE3", "category": "STOCK", "score": 0.90},
            {"ticker": "PETR4", "category": "STOCK", "score": 0.80}
        ]
        portfolio = allocator.compute_portfolio_weights(approved)
        total_weight = sum(p["weight"] for p in portfolio)
        self.assertAlmostEqual(total_weight, 1.0, places=2)

if __name__ == "__main__":
    unittest.main()
