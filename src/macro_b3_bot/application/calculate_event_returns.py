from __future__ import annotations

import logging
import math
from datetime import datetime, date, timedelta, timezone
from typing import Dict, Any, List, Tuple
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.adapters.historical_price_client import YahooFinanceProvider
from macro_b3_bot.application.market_session_scheduler import MarketSessionScheduler

logger = logging.getLogger(__name__)

def run_ols(x: List[float], y: List[float]) -> Tuple[float, float]:
    """
    Executa regressão linear simples por OLS: y = alpha + beta * x
    Retorna (alpha, beta).
    """
    n = len(x)
    if n < 2:
        return 0.0, 1.0  # Fallback

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    num = 0.0
    den = 0.0
    for i in range(n):
        num += (x[i] - mean_x) * (y[i] - mean_y)
        den += (x[i] - mean_x) ** 2

    if den == 0.0:
        return 0.0, 1.0

    beta = num / den
    alpha = mean_y - beta * mean_x
    return alpha, beta

class EventReturnsCalculator:
    """
    Executa os cálculos quantitativos do Event Study:
    - Ingestão de preços e benchmarks do DuckDB/YahooFinance
    - Mapeamento das datas de pregão efetivo B3
    - Estimação de alfa e beta no período de controle [-160, -20]
    - Cálculo de retornos anormais acumulados (CAR) em múltiplas janelas
    - Cálculo de z-score de volume
    """
    def __init__(self, settings: Settings, provider=None):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"
        self.provider = provider or YahooFinanceProvider()
        self.scheduler = MarketSessionScheduler()

    def run_calculator(self) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Garante a existência das tabelas auxiliares
        store._init_tables()

        # Busca eventos com tickers mapeados válidos (exclui UNKNOWN e códigos de CVM residuais)
        events = conn.execute(
            """
            SELECT ec.event_id, ec.ticker, ec.cvm_code, ec.publication_timestamp as delivery_date
            FROM event_candidates ec
            WHERE ec.ticker != 'UNKNOWN' AND TRY_CAST(ec.ticker AS INTEGER) IS NULL
            """
        ).fetchall()

        processed_count = 0
        error_count = 0
        total = len(events)

        logger.info(f"Iniciando calculo de retornos para {total} eventos.")

        for event_id, ticker, cvm_code, delivery_date in events:
            try:
                # 1. Determina a data base do evento
                pub_date = delivery_date.date() if isinstance(delivery_date, datetime) else date.fromisoformat(str(delivery_date)[:10])

                # Download de preços históricos do Ativo e do Ibovespa para a janela larga:
                # -180 pregões a +80 pregões (usamos +/- 280 dias civis como margem de segurança)
                start_fetch = pub_date - timedelta(days=280)
                end_fetch = pub_date + timedelta(days=120)

                # Ingestão do ativo e Ibovespa
                self._ingest_prices_to_db(store, ticker, start_fetch, end_fetch)
                self._ingest_prices_to_db(store, "^BVSP", start_fetch, end_fetch)

                # Carrega os preços do banco
                asset_prices = store.get_market_prices(ticker, start_fetch, end_fetch)
                ibov_prices = store.get_market_prices("^BVSP", start_fetch, end_fetch)

                if len(asset_prices) < 40 or len(ibov_prices) < 40:
                    logger.warning(f"Dados insuficientes para {ticker} ou ^BVSP na janela do evento {event_id}")
                    self._save_insufficient_data_outcome(store, event_id, ticker, delivery_date)
                    error_count += 1
                    continue

                # Lista de datas reais de negociação (usando o benchmark Ibovespa como calendário de referência)
                trading_dates = [p["trading_date"] for p in ibov_prices]

                # 2. Mapeamento do pregão efetivo
                eff_event = self.scheduler.compute_effective_dates(event_id, delivery_date, trading_dates)
                store.save_effective_market_event(eff_event.model_dump(mode="json"))

                # 3. Alinhamento de dados e cálculo de retornos diários
                # Cria mapas data -> preco_fechamento_ajustado
                asset_map = {p["trading_date"]: float(p["adjusted_close"] or p["close"]) for p in asset_prices}
                ibov_map = {p["trading_date"]: float(p["adjusted_close"] or p["close"]) for p in ibov_prices}
                volume_map = {p["trading_date"]: float(p["volume"] or 0) for p in asset_prices}

                # Cria série alinhada de negociações
                aligned_dates = sorted(list(set(asset_map.keys()).intersection(ibov_map.keys())))

                # Encontra o índice da data efetiva
                if eff_event.effective_trading_date not in aligned_dates:
                    logger.warning(f"Data efetiva {eff_event.effective_trading_date} nao encontrada nas datas alinhadas do evento {event_id}")
                    self._save_insufficient_data_outcome(store, event_id, ticker, delivery_date)
                    error_count += 1
                    continue

                event_idx = aligned_dates.index(eff_event.effective_trading_date)

                # Calcula os retornos simples diários
                asset_rets: List[float] = []
                ibov_rets: List[float] = []
                for i in range(1, len(aligned_dates)):
                    d_curr = aligned_dates[i]
                    d_prev = aligned_dates[i-1]
                    asset_rets.append((asset_map[d_curr] - asset_map[d_prev]) / asset_map[d_prev])
                    ibov_rets.append((ibov_map[d_curr] - ibov_map[d_prev]) / ibov_map[d_prev])

                # Ajusta o índice do evento na série de retornos (tamanho len(aligned_dates) - 1)
                event_ret_idx = event_idx - 1
                if event_ret_idx < 160 or event_ret_idx + 60 >= len(asset_rets):
                    logger.warning(f"Historico insuficiente em torno da data do evento para {ticker} (idx={event_ret_idx})")
                    self._save_insufficient_data_outcome(store, event_id, ticker, delivery_date)
                    error_count += 1
                    continue

                # 4. Estimação OLS do Market Model na janela de controle [-160, -20]
                control_start = event_ret_idx - 160
                control_end = event_ret_idx - 20
                x_control = ibov_rets[control_start:control_end+1]
                y_control = asset_rets[control_start:control_end+1]

                alpha, beta = run_ols(x_control, y_control)

                # Caso beta estimado seja espúrio ou nulo, aplica fallback para market-adjusted
                if math.isnan(beta) or abs(beta) > 5.0 or len(x_control) < 30:
                    alpha, beta = 0.0, 1.0

                # 5. Cálculo dos Retornos Brutos Acumulados
                raw_1d = asset_map[aligned_dates[event_idx + 1]] / asset_map[aligned_dates[event_idx]] - 1
                raw_5d = asset_map[aligned_dates[event_idx + 5]] / asset_map[aligned_dates[event_idx]] - 1
                raw_20d = asset_map[aligned_dates[event_idx + 20]] / asset_map[aligned_dates[event_idx]] - 1
                raw_60d = asset_map[aligned_dates[event_idx + 60]] / asset_map[aligned_dates[event_idx]] - 1

                # 6. Cálculo dos Retornos Anormais Acumulados (CAR)
                # CAR[a, b] = sum(R_asset_t - (alpha + beta * R_ibov_t))
                car_1d = self._sum_ar(asset_rets, ibov_rets, alpha, beta, event_ret_idx, 0, 1)
                car_5d = self._sum_ar(asset_rets, ibov_rets, alpha, beta, event_ret_idx, 0, 5)
                car_20d = self._sum_ar(asset_rets, ibov_rets, alpha, beta, event_ret_idx, 0, 20)
                car_60d = self._sum_ar(asset_rets, ibov_rets, alpha, beta, event_ret_idx, 0, 60)

                pre_event_car_5d = self._sum_ar(asset_rets, ibov_rets, alpha, beta, event_ret_idx, -5, -1)
                event_window_car = self._sum_ar(asset_rets, ibov_rets, alpha, beta, event_ret_idx, -1, 1)

                # 7. Parâmetros de Mercado & Volume
                # Volatilidade Histórica (desvio padrão anualizado dos retornos no período de controle)
                mean_ret = sum(y_control) / len(y_control)
                var_ret = sum((r - mean_ret) ** 2 for r in y_control) / (len(y_control) - 1)
                hist_vol = math.sqrt(var_ret) * math.sqrt(252)

                # Z-Score de Volume (dia do evento comparado à janela de controle)
                vols_control = [volume_map[aligned_dates[i]] for i in range(control_start, control_end + 1)]
                mean_vol = sum(vols_control) / len(vols_control)
                var_vol = sum((v - mean_vol) ** 2 for v in vols_control) / (len(vols_control) - 1)
                std_vol = math.sqrt(var_vol)

                vol_event = volume_map[aligned_dates[event_idx]]
                vol_z = (vol_event - mean_vol) / std_vol if std_vol > 0 else 0.0

                prior_close = asset_map[aligned_dates[event_idx - 1]]

                outcome = {
                    "event_id": event_id,
                    "ticker": ticker,
                    "publication_timestamp": delivery_date,
                    "effective_trading_date": eff_event.effective_trading_date,
                    "publication_session": eff_event.publication_session,
                    "prior_close": prior_close,
                    "raw_return_1d": raw_1d,
                    "raw_return_5d": raw_5d,
                    "raw_return_20d": raw_20d,
                    "raw_return_60d": raw_60d,
                    "car_1d": car_1d,
                    "car_5d": car_5d,
                    "car_20d": car_20d,
                    "car_60d": car_60d,
                    "pre_event_car_5d": pre_event_car_5d,
                    "event_window_car": event_window_car,
                    "beta": beta,
                    "historical_volatility": hist_vol,
                    "volume_zscore": vol_z,
                    "bootstrap_pvalue_1d": None,
                    "bootstrap_pvalue_5d": None,
                    "bootstrap_pvalue_20d": None,
                    "outcome_label": "INSUFFICIENT_DATA"  # Classificado pelo bootstrap na fase seguinte
                }

                store.save_event_market_outcome(outcome)
                processed_count += 1

            except Exception as e:
                logger.error(f"Erro ao processar retornos para evento {event_id} ({ticker}): {e}")
                self._save_insufficient_data_outcome(store, event_id, ticker, delivery_date)
                error_count += 1


        # Salva outcomes de INSUFFICIENT_DATA para candidatos com ticker UNKNOWN ou inválido
        invalid_events = conn.execute(
            """
            SELECT ec.event_id, ec.ticker, ec.cvm_code, MIN(i.delivery_date)
            FROM event_candidates ec
            LEFT JOIN evidence_claims c ON ec.claim_ids LIKE '%' || c.claim_id || '%'
            LEFT JOIN ipe_document_index i ON c.document_id = i.document_id
            WHERE ec.ticker = 'UNKNOWN' OR TRY_CAST(ec.ticker AS INTEGER) IS NOT NULL
            GROUP BY ec.event_id, ec.ticker, ec.cvm_code
            """
        ).fetchall()
        
        for event_id, ticker, cvm_code, delivery_date in invalid_events:
            d_date = delivery_date or datetime.now(timezone.utc)
            self._save_insufficient_data_outcome(store, event_id, ticker, d_date)

        store.close()
        return {
            "total_candidates": total + len(invalid_events),
            "processed": processed_count,
            "failed": error_count + len(invalid_events)
        }

    def _ingest_prices_to_db(self, store: DatabaseStore, ticker: str, start: date, end: date) -> None:
        """Salva no banco se não estiver presente na janela requisitada."""
        existing = store.get_market_prices(ticker, start, end)
        expected_days = (end - start).days
        # Se temos menos de 10% dos dias possíveis já cacheados, re-baixa
        if len(existing) < (expected_days * 0.1):
            prices = self.provider.fetch_prices(ticker, start, end)
            for p in prices:
                store.save_market_price(p.model_dump(mode="json"))

    def _sum_ar(self, asset_rets: List[float], ibov_rets: List[float], alpha: float, beta: float, event_ret_idx: int, start_offset: int, end_offset: int) -> float:
        total_ar = 0.0
        for offset in range(start_offset, end_offset + 1):
            idx = event_ret_idx + offset
            total_ar += asset_rets[idx] - (alpha + beta * ibov_rets[idx])
        return total_ar

    def _save_insufficient_data_outcome(self, store: DatabaseStore, event_id: str, ticker: str, delivery_date: datetime) -> None:
        pub_date = delivery_date.date() if isinstance(delivery_date, datetime) else date.fromisoformat(str(delivery_date)[:10])
        store.save_event_market_outcome({
            "event_id": event_id,
            "ticker": ticker,
            "publication_timestamp": delivery_date,
            "effective_trading_date": pub_date,
            "publication_session": "UNKNOWN",
            "prior_close": None,
            "raw_return_1d": None,
            "raw_return_5d": None,
            "raw_return_20d": None,
            "raw_return_60d": None,
            "car_1d": None,
            "car_5d": None,
            "car_20d": None,
            "car_60d": None,
            "pre_event_car_5d": None,
            "event_window_car": None,
            "beta": None,
            "historical_volatility": None,
            "volume_zscore": None,
            "bootstrap_pvalue_1d": None,
            "bootstrap_pvalue_5d": None,
            "bootstrap_pvalue_20d": None,
            "outcome_label": "INSUFFICIENT_DATA"
        })
