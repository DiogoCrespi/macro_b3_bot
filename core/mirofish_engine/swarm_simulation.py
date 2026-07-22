import sys
import json
from pathlib import Path
from typing import Dict, List, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import MAPPING_FILE

class SwarmSimulationEngine:
    """
    Engine de simulacao baseada na arquitetura MiroFish (Swarm Intelligence).
    Instancia personas de agentes especialistas para deduzir o impacto de choques
    macro, geopoliticos e climaticos sobre ativos da B3.
    """
    def __init__(self):
        self.mappings = self._load_sector_mappings()

    def _load_sector_mappings(self) -> Dict[str, Any]:
        if Path(MAPPING_FILE).exists():
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"drivers": {}}

    def run_simulation(self, macro_snapshot: Dict[str, Any], news_headlines: List[str]) -> Dict[str, Any]:
        """
        Executa a simulacao de enxame combinando dados quantitativos e narrativas.
        Returns:
            {
               "macro_regime": "RISK_OFF" | "RISK_ON" | "NEUTRAL",
               "active_drivers": [list of detected drivers],
               "favored_tickers": [list of tickers],
               "penalized_tickers": [list of tickers],
               "sector_scores": { "MINING_OIL": 0.8, ... },
               "swarm_thesis": "Descricao da tese do enxame"
            }
        """
        active_drivers = []
        favored_tickers = set()
        penalized_tickers = set()
        
        # 1. Agente MacroStrategist: Analisa DXY, S&P 500 e Ouro
        dxy_change = macro_snapshot.get("dxy", {}).get("change_pct", 0.0)
        sp500_change = macro_snapshot.get("sp500", {}).get("change_pct", 0.0)
        gold_change = macro_snapshot.get("gold", {}).get("change_pct", 0.0)
        oil_change = macro_snapshot.get("oil", {}).get("change_pct", 0.0)
        
        if dxy_change > 0.005 or sp500_change < -0.01:
            macro_regime = "RISK_OFF"
        elif sp500_change > 0.005 and dxy_change < 0:
            macro_regime = "RISK_ON"
        else:
            macro_regime = "NEUTRAL"
            
        # 2. Agente Commodities & Geopolitica
        if oil_change > 0.03 or gold_change > 0.015:
            active_drivers.append("SUPER_CICLO_COMMODITIES")
            
        if macro_regime == "RISK_OFF":
            active_drivers.append("ALTA_JUROS_FED_DXY")

        # 3. Agente Climatico & AgroAnalyst (Varredura de noticias para El Niño / Seca)
        headline_text = " ".join(news_headlines).lower()
        if "el nino" in headline_text or "niño" in headline_text or "seca" in headline_text or "agro" in headline_text:
            active_drivers.append("EL_NINO_SECAGEM")
            
        if "selic" in headline_text and ("corte" in headline_text or "reducao" in headline_text or "queda" in headline_text):
            active_drivers.append("CORTE_JUROS_BACEN")
            
        # Dedução de impacto nos ativos baseada no mapeamento setorial
        drivers_config = self.mappings.get("drivers", {})
        thesis_parts = [f"Regime de Mercado Detectado: {macro_regime}."]
        
        for driver in active_drivers:
            info = drivers_config.get(driver, {})
            if info:
                favored_tickers.update(info.get("beneficiaries", []))
                penalized_tickers.update(info.get("penalized", []))
                thesis_parts.append(f"Driver '{info.get('name')}': {info.get('reason')}")
                
        # Remocao de confluencias conflitantes (favored prevalece se houver choque positivo forte)
        penalized_tickers = list(penalized_tickers - favored_tickers)
        
        return {
            "macro_regime": macro_regime,
            "active_drivers": active_drivers,
            "favored_tickers": sorted(list(favored_tickers)),
            "penalized_tickers": sorted(list(penalized_tickers)),
            "swarm_thesis": " \n".join(thesis_parts)
        }

if __name__ == "__main__":
    engine = SwarmSimulationEngine()
    dummy_macro = {
        "dxy": {"change_pct": 0.006},
        "sp500": {"change_pct": -0.012},
        "gold": {"change_pct": 0.02},
        "oil": {"change_pct": 0.04}
    }
    dummy_news = [
        "El Niño Godzilla pressiona agro do Brasil e eleva risco para geradoras hidricas",
        "Alta do petroleo e tensao geopolitica impactam inflacao mundial"
    ]
    sim = engine.run_simulation(dummy_macro, dummy_news)
    print("Resultado da Simulacao de Enxame (MiroFish):")
    print(f"Regime: {sim['macro_regime']}")
    print(f"Ativos Favorecidos: {sim['favored_tickers']}")
    print(f"Ativos Penalizados: {sim['penalized_tickers']}")
    print(f"Tese do Enxame:\n{sim['swarm_thesis']}")
