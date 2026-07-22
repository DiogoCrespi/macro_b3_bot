import sys
import json
import urllib.request
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

class MacroApiClient:
    """
    Cliente para obtenção de indicadores macroeconômicos globais e commodities
    usando APIs públicas e endpoints gratuitos do Yahoo Finance / BACEN.
    """
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def fetch_yahoo_quote(self, symbol: str) -> Dict[str, float]:
        """Obtem cotacao atual e variacao percentual 24h para um símbolo do Yahoo Finance."""
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            
            result = data["chart"]["result"][0]
            meta = result["meta"]
            current_price = meta.get("regularMarketPrice", 0.0)
            previous_close = meta.get("chartPreviousClose", current_price)
            
            pct_change = ((current_price - previous_close) / previous_close) if previous_close else 0.0
            return {
                "symbol": symbol,
                "price": current_price,
                "previous_close": previous_close,
                "change_pct": pct_change
            }
        except Exception as e:
            # Fallback seguro
            return {
                "symbol": symbol,
                "price": 0.0,
                "previous_close": 0.0,
                "change_pct": 0.0,
                "error": str(e)
            }

    def get_global_macro_snapshot(self) -> Dict[str, Any]:
        """
        Retorna o panorama macro global:
        - DXY (Dólar Global)
        - USDBRL (Dólar no Brasil)
        - S&P 500 (Risco Global)
        - Ouro (Hedge)
        - Petróleo Brent (Commodity de energia)
        """
        symbols = {
            "dxy": "DX-Y.NYB",
            "usdbrl": "USDBRL=X",
            "sp500": "^GSPC",
            "gold": "GC=F",
            "oil": "CL=F",
            "us10y": "^TNX"
        }
        
        snapshot = {}
        for key, sym in symbols.items():
            snapshot[key] = self.fetch_yahoo_quote(sym)
            
        return snapshot

if __name__ == "__main__":
    client = MacroApiClient()
    snapshot = client.get_global_macro_snapshot()
    print("Snapshot Macro Global:")
    for k, v in snapshot.items():
        print(f"  {k.upper()}: Preco={v.get('price')} | Variacao 24h={v.get('change_pct'):.2%}")
