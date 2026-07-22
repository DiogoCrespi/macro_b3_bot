import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

class ExecutiveReportGenerator:
    """
    Gerador de relatorios executivos de alocacao dinamica ("Compre X hoje por causa do fator Y").
    """
    def __init__(self):
        pass

    def generate_report(
        self,
        portfolio: List[Dict[str, Any]],
        swarm_sim: Dict[str, Any],
        macro_snapshot: Dict[str, Any]
    ) -> str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        lines = []
        lines.append("==========================================================================")
        lines.append("   MACRO B3 BOT - RELATORIO DE ALOCACAO E TESE TASTICA DE MERCADO")
        lines.append(f"   Data de Emissao: {now_str}")
        lines.append("==========================================================================")
        lines.append("")
        lines.append("1. PANORAMA MACROECONOMICO GLOBAL (INDICADORES CHAVE)")
        lines.append("--------------------------------------------------------------------------")
        lines.append(f"   - Dolar Global (DXY): {macro_snapshot.get('dxy', {}).get('price')} ({macro_snapshot.get('dxy', {}).get('change_pct', 0.0):.2%})")
        lines.append(f"   - S&P 500 (EUA): {macro_snapshot.get('sp500', {}).get('price')} ({macro_snapshot.get('sp500', {}).get('change_pct', 0.0):.2%})")
        lines.append(f"   - Petroleo Brent: ${macro_snapshot.get('oil', {}).get('price')} ({macro_snapshot.get('oil', {}).get('change_pct', 0.0):.2%})")
        lines.append(f"   - Ouro (Hedge): ${macro_snapshot.get('gold', {}).get('price')} ({macro_snapshot.get('gold', {}).get('change_pct', 0.0):.2%})")
        lines.append(f"   - Dolar/Real (USDBRL): R$ {macro_snapshot.get('usdbrl', {}).get('price')} ({macro_snapshot.get('usdbrl', {}).get('change_pct', 0.0):.2%})")
        lines.append("")
        lines.append("2. TESE E SIMULACAO DE ENXAME (MIROFISH ENGINE)")
        lines.append("--------------------------------------------------------------------------")
        lines.append(f"   Regime Detectado: {swarm_sim.get('macro_regime')}")
        lines.append(f"   Drivers Ativos: {', '.join(swarm_sim.get('active_drivers', []))}")
        lines.append("   Detalhamento da Tese:")
        lines.append(f"   {swarm_sim.get('swarm_thesis')}")
        lines.append("")
        lines.append("3. RECOMENDACAO EXECUTIVA DE CARTEIRA (PESOS DE ALOCACAO)")
        lines.append("--------------------------------------------------------------------------")
        lines.append("   [PESO %]  | ATIVO     | CATEGORIA          | JUSTIFICATIVA PRINCIPAL")
        lines.append("   ----------|-----------|--------------------|-----------------------------------")
        
        for item in portfolio:
            ticker = item.get("ticker", "").ljust(9)
            cat = item.get("category", "").ljust(18)
            weight_pct = f"{item.get('weight', 0.0):.1%}".rjust(8)
            
            if ticker.strip() in swarm_sim.get("favored_tickers", []):
                reason = "Favorecido por Tese Macro/Climatica + Qualidade Fundamentalista"
            elif item.get("ticker") == "TESOURO_SELIC":
                reason = "Liquidez Diaria / Protecao de Caixa"
            else:
                reason = "Aprovado por Múltiplos Fundamentalistas e Consenso do Tribunal"
                
            lines.append(f"   {weight_pct} | {ticker} | {cat} | {reason}")
            
        lines.append("==========================================================================")
        return "\n".join(lines)

if __name__ == "__main__":
    gen = ExecutiveReportGenerator()
    dummy_portfolio = [
        {"ticker": "VALE3", "category": "STOCK", "weight": 0.15},
        {"ticker": "PETR4", "category": "STOCK", "weight": 0.15},
        {"ticker": "AGRO3", "category": "STOCK", "weight": 0.15},
        {"ticker": "HGLG11", "category": "FII", "weight": 0.15},
        {"ticker": "TESOURO_SELIC", "category": "CASH", "weight": 0.40}
    ]
    dummy_sim = {
        "macro_regime": "NEUTRAL",
        "active_drivers": ["SUPER_CICLO_COMMODITIES", "EL_NINO_SECAGEM"],
        "favored_tickers": ["VALE3", "PETR4", "AGRO3"],
        "swarm_thesis": "Alta do petroleo e celulose favorecem exportadoras."
    }
    dummy_macro = {
        "dxy": {"price": 101.2, "change_pct": 0.002},
        "sp500": {"price": 7500.0, "change_pct": -0.005},
        "oil": {"price": 84.6, "change_pct": 0.07},
        "gold": {"price": 4089.0, "change_pct": 0.02},
        "usdbrl": {"price": 5.07, "change_pct": -0.003}
    }
    report = gen.generate_report(dummy_portfolio, dummy_sim, dummy_macro)
    print(report)
