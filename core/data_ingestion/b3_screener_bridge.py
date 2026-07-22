import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Any

# Garantir que a raiz do projeto esteja no sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import B3_SCREENER_DIR

class B3ScreenerBridge:
    """
    Bridge para consumir dados de Ações, FIIs, ETFs e Tesouro do projeto b3_screener.
    """
    def __init__(self, screener_dir: Path = B3_SCREENER_DIR):
        self.screener_dir = Path(screener_dir)
        self.data_js_path = self.screener_dir / "data.js"
        self._cached_data: Dict[str, Any] = {}

    def load_data(self, force_reload: bool = False) -> Dict[str, Any]:
        """Carrega e analisa o arquivo data.js do b3_screener."""
        if self._cached_data and not force_reload:
            return self._cached_data

        if not self.data_js_path.exists():
            raise FileNotFoundError(f"Arquivo data.js nao encontrado em {self.data_js_path}")

        with open(self.data_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extrai o objeto JSON de "window.INVEST_DATA = { ... };"
        json_match = re.search(r"window\.INVEST_DATA\s*=\s*(\{.*\});?", content, re.DOTALL)
        if not json_match:
            raise ValueError(f"Formato invalido em {self.data_js_path}")

        json_str = json_match.group(1).strip()
        if json_str.endswith(";"):
            json_str = json_str[:-1]

        self._cached_data = json.loads(json_str)
        return self._cached_data

    def get_stocks(self) -> List[Dict[str, Any]]:
        """Retorna lista de acoes com multiplos fundamentalistas."""
        data = self.load_data()
        return data.get("stocks", [])

    def get_fiis(self) -> List[Dict[str, Any]]:
        """Retorna lista de FIIs analisados."""
        data = self.load_data()
        return data.get("fiis", [])

    def get_etfs(self) -> List[Dict[str, Any]]:
        """Retorna lista de ETFs."""
        data = self.load_data()
        return data.get("etfs", [])

    def get_ticker_info(self, ticker: str) -> Dict[str, Any]:
        """Busca informacoes detalhadas de um ticker especifico."""
        ticker = ticker.upper()
        data = self.load_data()
        
        for category in ["stocks", "fiis", "etfs"]:
            for asset in data.get(category, []):
                if asset.get("ticker") == ticker:
                    asset["category"] = category
                    return asset
        return {}

if __name__ == "__main__":
    bridge = B3ScreenerBridge()
    data = bridge.load_data()
    print(f"Data JS carregado. Data de atualizacao: {data.get('updatedAt')}")
    print(f"Total de acoes: {len(bridge.get_stocks())} | Total FIIs: {len(bridge.get_fiis())}")
