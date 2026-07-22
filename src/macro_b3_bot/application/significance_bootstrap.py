from __future__ import annotations

import logging
import random
from typing import Dict, Any, List
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

class SignificanceBootstrapper:
    """
    Calcula p-values empíricos usando Simulação Bootstrap para
    classificar a reação de mercado em CONFIRMED, WEAK_SIGNAL ou NOISE.
    """
    def __init__(self, settings: Settings, iterations: int = 2000, seed: int = 42):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"
        self.iterations = iterations
        self.seed = seed

    def run_bootstrap(self) -> Dict[str, Any]:
        random.seed(self.seed)
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Carrega resultados do event study que não foram classificados ou estão em cache inicial
        outcomes = conn.execute(
            """
            SELECT event_id, ticker, effective_trading_date, prior_close,
                   car_1d, car_5d, car_20d, beta
            FROM event_market_outcomes
            WHERE car_5d IS NOT NULL
            """
        ).fetchall()

        updated_count = 0
        total = len(outcomes)
        
        # Lista temporária para guardar os p-values brutos calculados
        raw_results = []

        for event_id, ticker, eff_date, prior_close, car_1d, car_5d, car_20d, beta in outcomes:
            # 1. Carrega o histórico de preços do ativo e Ibovespa do banco de dados
            # para recalcular os retornos anormais da janela de estimação
            start_date = eff_date - type(eff_date).resolution * 280
            end_date = eff_date + type(eff_date).resolution * 120

            asset_prices = store.get_market_prices(ticker, start_date, end_date)
            ibov_prices = store.get_market_prices("^BVSP", start_date, end_date)

            if len(asset_prices) < 40 or len(ibov_prices) < 40:
                continue

            asset_map = {p["trading_date"]: float(p["adjusted_close"] or p["close"]) for p in asset_prices}
            ibov_map = {p["trading_date"]: float(p["adjusted_close"] or p["close"]) for p in ibov_prices}

            aligned_dates = sorted(list(set(asset_map.keys()).intersection(ibov_map.keys())))
            if eff_date not in aligned_dates:
                continue

            event_idx = aligned_dates.index(eff_date)
            event_ret_idx = event_idx - 1

            # Calcula retornos diários alinhados
            asset_rets: List[float] = []
            ibov_rets: List[float] = []
            for i in range(1, len(aligned_dates)):
                d_curr = aligned_dates[i]
                d_prev = aligned_dates[i-1]
                asset_rets.append((asset_map[d_curr] - asset_map[d_prev]) / asset_map[d_prev])
                ibov_rets.append((ibov_map[d_curr] - ibov_map[d_prev]) / ibov_map[d_prev])

            # Recupera alfa do OLS
            control_start = event_ret_idx - 160
            control_end = event_ret_idx - 20
            x_control = ibov_rets[control_start:control_end+1]
            y_control = asset_rets[control_start:control_end+1]

            mean_x = sum(x_control) / len(x_control)
            mean_y = sum(y_control) / len(y_control)
            alpha = mean_y - beta * mean_x

            # Série de Retornos Anormais (Excess Returns) na janela de controle
            excess_returns = [y_control[i] - (alpha + beta * x_control[i]) for i in range(len(x_control))]

            # 2. Executa bootstrap para as janelas: 1 dia, 5 dias, 20 dias
            p_1d = self._bootstrap_p(car_1d, excess_returns, n_days=1)
            p_5d = self._bootstrap_p(car_5d, excess_returns, n_days=5)
            p_20d = self._bootstrap_p(car_20d, excess_returns, n_days=20)
            
            raw_results.append({
                "event_id": event_id,
                "ticker": ticker,
                "car_1d": car_1d,
                "car_5d": car_5d,
                "car_20d": car_20d,
                "p_1d": p_1d,
                "p_5d": p_5d,
                "p_20d": p_20d
            })

        # 3. Correção por múltiplos testes: Benjamini-Hochberg (BH) no p_5d
        M = len(raw_results)
        if M > 0:
            # Ordena pelo p_5d bruto em ordem crescente
            raw_results.sort(key=lambda x: x["p_5d"])
            
            # Calcula o p-value ajustado por BH (de trás para a frente para manter monotonicidade)
            for idx in range(M):
                rank = idx + 1
                raw_results[idx]["rank"] = rank
                raw_results[idx]["bh_threshold"] = (rank / M) * 0.10  # FDR = 10%
                raw_results[idx]["bh_adjusted_p"] = min(1.0, raw_results[idx]["p_5d"] * (M / rank))
            
            # Monotonicidade retrospectiva
            for idx in range(M - 2, -1, -1):
                raw_results[idx]["bh_adjusted_p"] = min(raw_results[idx]["bh_adjusted_p"], raw_results[idx+1]["bh_adjusted_p"])

            # Regra de BH: encontra o maior rank k onde p_5d <= bh_threshold
            k_idx = -1
            for idx in range(M - 1, -1, -1):
                if raw_results[idx]["p_5d"] <= raw_results[idx]["bh_threshold"]:
                    k_idx = idx
                    break

            # 4. Aplica classificação final e atualiza banco
            for idx, item in enumerate(raw_results):
                event_id = item["event_id"]
                ticker = item["ticker"]
                car_5d = item["car_5d"]
                p_5d = item["p_5d"]
                
                # CONFIRMED se:
                # - passou no critério BH (índice <= k_idx)
                # - magnitude da reação |CAR_5d| >= 1.0%
                if idx <= k_idx and abs(car_5d) >= 0.01:
                    label = "CONFIRMED"
                elif abs(car_5d) >= 0.005 or p_5d <= 0.20:
                    label = "WEAK_SIGNAL"
                else:
                    label = "NOISE"

                # Atualiza o banco de dados
                conn.execute(
                    """
                    UPDATE event_market_outcomes
                    SET bootstrap_pvalue_1d = ?,
                        bootstrap_pvalue_5d = ?,
                        bootstrap_pvalue_20d = ?,
                        bh_adjusted_pvalue_5d = ?,
                        bh_threshold_5d = ?,
                        outcome_label = ?,
                        calculated_at = CURRENT_TIMESTAMP
                    WHERE event_id = ? AND ticker = ?
                    """,
                    [
                        item["p_1d"],
                        item["p_5d"],
                        item["p_20d"],
                        item["bh_adjusted_p"],
                        item["bh_threshold"],
                        label,
                        event_id,
                        ticker
                    ]
                )

                # Também atualiza o status correspondente no event_candidates
                # Para manter sincronismo de classificação
                conn.execute(
                    "UPDATE event_candidates SET status = ? WHERE event_id = ?",
                    [label, event_id]
                )

                updated_count += 1

        store.close()
        logger.info(f"Bootstrap completo. {updated_count} eventos analisados e classificados.")
        return {
            "total_outcomes": total,
            "bootstrapped": updated_count
        }

    def _bootstrap_p(self, observed_car: float, excess_returns: List[float], n_days: int) -> float:
        if not excess_returns or len(excess_returns) < n_days:
            return 1.0

        extreme_count = 0
        for _ in range(self.iterations):
            # Desenha n_days aleatórios da série de retornos anormais sob H0
            sample = random.choices(excess_returns, k=n_days)
            boot_car = sum(sample)
            if abs(boot_car) >= abs(observed_car):
                extreme_count += 1

        return (extreme_count + 1) / (self.iterations + 1)
