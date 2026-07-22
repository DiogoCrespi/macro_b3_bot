from __future__ import annotations

import json
import logging
from datetime import date
from typing import Dict, Any
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

class ScoreRecalibrator:
    """
    Recalibra os scores de Novelty e Materiality dos EventCandidates:
    - Novelty: baseado na distância temporal para eventos semelhantes da mesma empresa
    - Materiality: baseado em denominadores fundamentalistas reais (ex: Ativo Total, preço da ação)
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def run_recalibration(self) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Carrega o cache de informações de mercado (totalDebt, marketCap)
        cache_path = self.settings.data_dir / "market_info_cache.json"
        cache = {}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Carrega todos os EventCandidates
        candidates = conn.execute(
            """
            SELECT event_id, ticker, cvm_code, event_type, effective_date,
                   novelty_score, materiality_score, quantitative_impact
            FROM event_candidates
            """
        ).fetchall()

        recalibrated_count = 0
        total = len(candidates)

        for event_id, ticker, cvm_code, event_type, eff_date, old_nov, old_mat, quant_json in candidates:
            # Parse do quant impact
            quant_impact = {}
            if quant_json:
                try:
                    if isinstance(quant_json, str):
                        quant_impact = json.loads(quant_json)
                        if isinstance(quant_impact, str):
                            quant_impact = json.loads(quant_impact)
                    else:
                        quant_impact = quant_json
                except Exception:
                    pass

            val_impact = 0.0
            if isinstance(quant_impact, dict) and quant_impact:
                try:
                    val_impact = float(list(quant_impact.values())[0])
                except (ValueError, TypeError, IndexError):
                    pass

            # Procura no cache
            market_symbol = f"{ticker}.SA"
            if ticker == "GUAR3":
                market_symbol = "RIAA3.SA"
            info = cache.get(market_symbol, {})
            market_cap = info.get("marketCap")
            total_debt = info.get("totalDebt")

            # 1. Recalibração de Novelty
            # Procura o evento anterior do mesmo tipo e empresa
            prev_event = conn.execute(
                """
                SELECT effective_date FROM event_candidates
                WHERE cvm_code = ? AND event_type = ? AND effective_date < ?
                ORDER BY effective_date DESC
                LIMIT 1
                """,
                [cvm_code, event_type, eff_date]
            ).fetchone()

            if not prev_event:
                new_novelty = 1.0
            else:
                p_date = prev_event[0]
                if isinstance(p_date, str):
                    p_date = date.fromisoformat(p_date[:10])
                e_date = eff_date
                if isinstance(e_date, str):
                    e_date = date.fromisoformat(e_date[:10])
                
                days_diff = (e_date - p_date).days
                if days_diff > 365:
                    new_novelty = 0.8
                elif days_diff > 180:
                    new_novelty = 0.6
                elif days_diff > 60:
                    new_novelty = 0.4
                else:
                    new_novelty = 0.2

            # 2. Recalibração de Materiality
            new_materiality = old_mat  # Fallback

            if event_type in ("DIVIDEND_DECLARED", "JCP_DECLARED"):
                # yield = valor_por_acao / prior_close
                prior_close = conn.execute(
                    "SELECT prior_close FROM event_market_outcomes WHERE event_id = ? AND ticker = ?",
                    [event_id, ticker]
                ).fetchone()

                p_close = None
                if prior_close and prior_close[0] is not None:
                    p_close = float(prior_close[0])
                else:
                    # Tenta pegar cotacao do b3_screener se disponível
                    snap = conn.execute(
                        "SELECT price FROM asset_snapshots WHERE ticker = ? ORDER BY as_of DESC LIMIT 1",
                        [ticker]
                    ).fetchone()
                    if snap:
                        p_close = float(snap[0])

                if p_close and p_close > 0.0 and val_impact > 0.0:
                    # Dividendo por ação no piloto costuma ser pequeno, multiplicamos por 15.0 para normalizar o score
                    dy = val_impact / p_close
                    new_materiality = min(1.0, max(0.1, dy * 15.0))
            
            elif event_type == "DEBT_ISSUANCE":
                # ratio = valor da emissão / dívida bruta anterior
                if total_debt and total_debt > 0.0 and val_impact > 0.0:
                    ratio = val_impact / float(total_debt)
                    # Normaliza multiplicando por 20.0 (emissão de 5% da dívida total atinge score 1.0)
                    new_materiality = min(1.0, max(0.1, ratio * 20.0))
                else:
                    # Fallback para Ativo Total
                    assets_row = conn.execute(
                        """
                        SELECT value, scale FROM financial_statement_lines
                        WHERE document_id IN (
                            SELECT document_id FROM cvm_documents WHERE cvm_code = ?
                        )
                          AND account_code = '1'
                          AND fiscal_order = 'ÚLTIMO'
                        ORDER BY end_date DESC
                        LIMIT 1
                        """,
                        [cvm_code]
                    ).fetchone()
                    if assets_row:
                        val_assets = float(assets_row[0]) * float(assets_row[1])
                        if val_assets > 0.0 and val_impact > 0.0:
                            ratio = val_impact / val_assets
                            new_materiality = min(1.0, max(0.1, ratio * 10.0))

            elif event_type == "CAPITAL_INCREASE":
                # ratio = valor captado / market cap anterior
                if market_cap and market_cap > 0.0 and val_impact > 0.0:
                    ratio = val_impact / float(market_cap)
                    # Normaliza multiplicando por 10.0 (captação de 10% do market cap atinge score 1.0)
                    new_materiality = min(1.0, max(0.1, ratio * 10.0))
                else:
                    # Fallback para Ativo Total
                    assets_row = conn.execute(
                        """
                        SELECT value, scale FROM financial_statement_lines
                        WHERE document_id IN (
                            SELECT document_id FROM cvm_documents WHERE cvm_code = ?
                        )
                          AND account_code = '1'
                          AND fiscal_order = 'ÚLTIMO'
                        ORDER BY end_date DESC
                        LIMIT 1
                        """,
                        [cvm_code]
                    ).fetchone()
                    if assets_row:
                        val_assets = float(assets_row[0]) * float(assets_row[1])
                        if val_assets > 0.0 and val_impact > 0.0:
                            ratio = val_impact / val_assets
                            new_materiality = min(1.0, max(0.1, ratio * 10.0))

            # Atualiza no banco de dados
            conn.execute(
                """
                UPDATE event_candidates
                SET novelty_score = ?,
                    materiality_score = ?,
                    persistence_score = 0.8
                WHERE event_id = ?
                """,
                [round(new_novelty, 3), round(new_materiality, 3), event_id]
            )
            recalibrated_count += 1

        store.close()
        logger.info(f"Recalibração completa para {recalibrated_count} candidatos.")
        return {
            "total_candidates": total,
            "recalibrated": recalibrated_count
        }
