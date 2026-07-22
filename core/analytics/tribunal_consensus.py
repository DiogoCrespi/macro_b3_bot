import sys
from pathlib import Path
from typing import Dict, List, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import (
    RISK_OFF_VETO_DXY_CHANGE_24H,
    RISK_OFF_VETO_SP500_CHANGE_24H
)

class MacroTribunalConsensus:
    """
    Tribunal de Consenso Macro & Alocacao de Ativos.
    Agrega as visões do Agente Macro (MiroFish), Agente Fundamentalista e Agente de Risco.
    """
    def __init__(self):
        pass

    def evaluate_allocation_consensus(
        self,
        macro_sim: Dict[str, Any],
        ranked_assets: List[Dict[str, Any]],
        macro_snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Avalia o conjunto de sinais e aplica o Poder de Veto de Risco em cenários extremos.
        """
        dxy_change = macro_snapshot.get("dxy", {}).get("change_pct", 0.0)
        sp500_change = macro_snapshot.get("sp500", {}).get("change_pct", 0.0)
        
        # 1. GATEKEEPER DE RISCO EXTREMO (Veto de Entrada agressiva em Acoes)
        is_macro_veto = False
        veto_reason = ""
        
        if dxy_change >= RISK_OFF_VETO_DXY_CHANGE_24H:
            is_macro_veto = True
            veto_reason = f"VETO MACRO: Dolar Subindo Fortemente ({dxy_change:.2%})"
        elif sp500_change <= RISK_OFF_VETO_SP500_CHANGE_24H:
            is_macro_veto = True
            veto_reason = f"VETO MACRO: Mercado Global em Forte Queda (S&P 500 {sp500_change:.2%})"
            
        final_allocations = []
        
        if is_macro_veto:
            # Em cenario de VETO, aloca predominantemente em Caixa / Dólar / Tesouro
            return {
                "status": "VETO_DEFENSIVO",
                "reason": veto_reason,
                "recommended_portfolio": [
                    {"ticker": "USDBRL/IVVB11", "weight": 0.40, "reason": "Protecao Cambial"},
                    {"ticker": "TESOURO_SELIC", "weight": 0.60, "reason": "Caixa / Liquidez Diaria"}
                ]
            }

        # 2. VOTAÇÃO DE CONSENSO ENTRE AGENTES
        # Filtra ativos aprovados com score >= 0.60
        approved_assets = [a for a in ranked_assets if a.get("score", 0.0) >= 0.60]
        
        if not approved_assets:
            return {
                "status": "NEUTRO",
                "reason": "Nenhum ativo atingiu a nota de corte fundamentalista.",
                "recommended_portfolio": [
                    {"ticker": "TESOURO_SELIC", "weight": 1.0, "reason": "Caixa Integral"}
                ]
            }

        return {
            "status": "APROVADO",
            "reason": f"Consenso Aprovado com {len(approved_assets)} ativos alinhados com a tese macro.",
            "approved_assets": approved_assets
        }

if __name__ == "__main__":
    tribunal = MacroTribunalConsensus()
    sim = {"macro_regime": "NEUTRAL", "active_drivers": ["SUPER_CICLO_COMMODITIES"]}
    assets = [
        {"ticker": "VALE3", "category": "STOCK", "score": 0.90},
        {"ticker": "PETR4", "category": "STOCK", "score": 0.85},
        {"ticker": "HGLG11", "category": "FII", "score": 0.80}
    ]
    snapshot = {"dxy": {"change_pct": 0.002}, "sp500": {"change_pct": 0.001}}
    res = tribunal.evaluate_allocation_consensus(sim, assets, snapshot)
    print("Resultado do Tribunal de Consenso:")
    print(f"Status: {res['status']} | Razao: {res['reason']}")
