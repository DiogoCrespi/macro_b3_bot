import sys
from pathlib import Path
from typing import Dict, List, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import MAX_SINGLE_ASSET_WEIGHT, MIN_CASH_WEIGHT

class PortfolioAllocator:
    """
    Calcula a alocacao ideal de portfolio (% por ativo) com base nos scores dos ativos
    e restricoes institucionais de risco.
    """
    def __init__(self):
        self.max_asset_weight = MAX_SINGLE_ASSET_WEIGHT
        self.min_cash_weight = MIN_CASH_WEIGHT

    def compute_portfolio_weights(self, approved_assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Gera a carteira ponderada por score.
        """
        if not approved_assets:
            return [{"ticker": "TESOURO_SELIC", "weight": 1.0, "reason": "Caixa 100%"}]

        total_score = sum(a.get("score", 0.5) for a in approved_assets)
        equity_budget = 1.0 - self.min_cash_weight
        
        allocated_items = []
        remaining_budget = equity_budget

        for a in approved_assets:
            raw_weight = (a.get("score", 0.5) / total_score) * equity_budget
            capped_weight = min(raw_weight, self.max_asset_weight)
            
            allocated_items.append({
                "ticker": a.get("ticker"),
                "category": a.get("category"),
                "weight": round(capped_weight, 4),
                "score": a.get("score")
            })
            remaining_budget -= capped_weight

        # Aloca o restante do caixa no Tesouro Selic
        total_cash = self.min_cash_weight + max(0.0, remaining_budget)
        allocated_items.append({
            "ticker": "TESOURO_SELIC",
            "category": "CASH/FIXED_INCOME",
            "weight": round(total_cash, 4),
            "score": 1.0
        })

        return allocated_items

if __name__ == "__main__":
    allocator = PortfolioAllocator()
    assets = [
        {"ticker": "VALE3", "category": "STOCK", "score": 0.90},
        {"ticker": "PETR4", "category": "STOCK", "score": 0.85},
        {"ticker": "AGRO3", "category": "STOCK", "score": 0.80},
        {"ticker": "HGLG11", "category": "FII", "score": 0.75},
        {"ticker": "IVVB11", "category": "ETF", "score": 0.70}
    ]
    weights = allocator.compute_portfolio_weights(assets)
    print("Carteira Recomendada:")
    for w in weights:
        print(f"  {w['ticker']}: {w['weight']:.2%}")
