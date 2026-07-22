import sys
from pathlib import Path
from typing import Dict, List, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.data_ingestion.b3_screener_bridge import B3ScreenerBridge

class AssetScreener:
    """
    Screener de Qualidade Fundamentalista para Acoes, FIIs e ETFs.
    Filtra tickers candidatos a partir do b3_screener para garantir liquidez
    e saude financeira.
    """
    def __init__(self, bridge: B3ScreenerBridge = None):
        self.bridge = bridge or B3ScreenerBridge()

    def filter_and_rank_candidates(self, favored_tickers: List[str]) -> List[Dict[str, Any]]:
        """
        Recebe os tickers sugeridos pelo MiroFish e valida fundamentalmente.
        """
        validated_assets = []
        
        for ticker in favored_tickers:
            info = self.bridge.get_ticker_info(ticker)
            if not info:
                # Ticker nao encontrado ou ETF/Macro genérico
                validated_assets.append({
                    "ticker": ticker,
                    "category": "MACRO_INDEX/ETF",
                    "score": 0.70,
                    "reason": "Ativo macro/ETF sem multiplos acionarios diretos."
                })
                continue
                
            category = info.get("category")
            if category == "stocks":
                # Filtro para Acoes
                liq = info.get("liq_2meses", 0)
                pl = info.get("pl", 0)
                pvp = info.get("p_vp", 0)
                dy = info.get("dividend_yield", 0)
                roic = info.get("roic", 0)
                
                # Criterio de corte minimo
                if liq < 300000: # Liquidez < 300k
                    continue
                    
                score = 0.50
                if 0 < pl < 15: score += 0.15
                if 0 < pvp < 2.0: score += 0.15
                if roic > 10: score += 0.10
                if dy > 5.0: score += 0.10
                
                validated_assets.append({
                    "ticker": ticker,
                    "category": "STOCK",
                    "score": min(1.0, score),
                    "cotacao": info.get("cotacao"),
                    "pl": pl,
                    "p_vp": pvp,
                    "dy": dy,
                    "roic": roic,
                    "liq": liq
                })
                
            elif category == "fiis":
                # Filtro para FIIs
                pvp = info.get("p_vp", 1.0)
                dy = info.get("dividend_yield", 0)
                
                score = 0.60
                if 0.75 <= pvp <= 1.05: score += 0.20
                if dy > 8.0: score += 0.20
                
                validated_assets.append({
                    "ticker": ticker,
                    "category": "FII",
                    "score": min(1.0, score),
                    "cotacao": info.get("cotacao"),
                    "p_vp": pvp,
                    "dy": dy
                })
            else:
                validated_assets.append({
                    "ticker": ticker,
                    "category": "ETF/OUTROS",
                    "score": 0.75,
                    "cotacao": info.get("cotacao", 0)
                })
                
        # Ordena por Score Fundamentalista decrescente
        validated_assets.sort(key=lambda x: x.get("score", 0), reverse=True)
        return validated_assets

if __name__ == "__main__":
    screener = AssetScreener()
    candidates = ["VALE3", "PETR4", "AGRO3", "HGLG11", "IVVB11"]
    ranked = screener.filter_and_rank_candidates(candidates)
    print("Ativos Filtrados e Ranqueados pelo Screener:")
    for r in ranked:
        print(f"  {r['ticker']} ({r['category']}) - Score: {r['score']:.2f}")
