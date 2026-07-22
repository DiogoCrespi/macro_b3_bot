"""
Sprint 3A: Validação de Reação de Mercado

Para cada EventCandidate, busca preços históricos via yfinance e calcula:
- Retorno anormal acumulado (CAR) nos janelas [0,+1], [0,+3], [0,+5], [-1,+1]
- Usando retorno do IBOV como baseline (mercado)
- p-value empírico por bootstrap (H0: CAR = 0)
- Classifica o evento como CONFIRMED, WEAK_SIGNAL, NOISE ou NO_DATA

Limitações documentadas:
- yfinance não garante dados intraday para datas históricas antigas
- Dividendos já ajustados automaticamente pelo yfinance (usar auto_adjust=False para ex-div)
- Bootstrap com N=1000 resamples para p-value
"""
from __future__ import annotations

import json
import random
from datetime import datetime, date, timedelta, timezone
from typing import Dict, Any, List, Optional

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _compute_car(
    ticker_returns: List[float],
    market_returns: List[float],
    window_start: int,
    window_end: int,
    event_idx: int,
) -> Optional[float]:
    """
    CAR = Σ (ticker_ret - market_ret) para o intervalo [event_idx + window_start, event_idx + window_end].
    Retorna None se não há dados suficientes.
    """
    total = 0.0
    count = 0
    for offset in range(window_start, window_end + 1):
        idx = event_idx + offset
        if 0 <= idx < len(ticker_returns) and 0 <= idx < len(market_returns):
            total += ticker_returns[idx] - market_returns[idx]
            count += 1
    return total if count > 0 else None


def _bootstrap_pvalue(car: float, ticker_returns: List[float], market_returns: List[float], n_days: int, n_boot: int = 1000) -> float:
    """
    p-value bootstrap: proporção de resamples onde |CAR_boot| >= |CAR_observed|.
    """
    if not ticker_returns or not market_returns:
        return 1.0

    min_len = min(len(ticker_returns), len(market_returns))
    if min_len < n_days:
        return 1.0

    excess = [ticker_returns[i] - market_returns[i] for i in range(min_len)]
    extreme_count = 0

    for _ in range(n_boot):
        sample_indices = random.choices(range(min_len), k=n_days)
        car_boot = sum(excess[i] for i in sample_indices)
        if abs(car_boot) >= abs(car):
            extreme_count += 1

    return extreme_count / n_boot


class MarketReactionValidator:
    """
    Valida EventCandidates contra reação de mercado usando yfinance.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def validate_batch(self, limit: int = 100) -> Dict[str, Any]:
        try:
            import yfinance as yf
        except ImportError:
            return {
                "error": "yfinance não instalado. Execute: pip install yfinance",
                "validated": 0
            }

        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Pega EventCandidates com tickers reais e datas de efetivação
        rows = conn.execute("""
            SELECT event_id, ticker, cvm_code, event_type, effective_date, materiality_score
            FROM event_candidates
            WHERE status = 'CANDIDATE'
              AND effective_date IS NOT NULL
              AND ticker NOT LIKE '%|%'
            ORDER BY materiality_score DESC
            LIMIT ?
        """, [limit]).fetchall()

        validated = 0
        confirmed = 0
        weak = 0
        noise = 0
        no_data = 0

        # Cache de preços para evitar downloads repetidos
        price_cache: Dict[str, Any] = {}

        for event_id, ticker, cvm_code, event_type, effective_date, mat_score in rows:
            if effective_date is None:
                no_data += 1
                continue

            # Adiciona .SA para tickers B3 se necessário
            yf_ticker = ticker if "." in ticker else f"{ticker}.SA"
            yf_ibov = "^BVSP"

            # Janela de análise: -10 dias a +10 dias em torno do evento
            if isinstance(effective_date, str):
                event_dt = date.fromisoformat(effective_date[:10])
            else:
                event_dt = effective_date

            start = event_dt - timedelta(days=20)
            end   = event_dt + timedelta(days=15)

            # Download de preços
            try:
                if yf_ticker not in price_cache:
                    tk_data = yf.download(
                        yf_ticker,
                        start=start.isoformat(),
                        end=end.isoformat(),
                        auto_adjust=True,
                        progress=False
                    )
                    price_cache[yf_ticker] = tk_data

                if yf_ibov not in price_cache:
                    ibov_data = yf.download(
                        yf_ibov,
                        start=start.isoformat(),
                        end=end.isoformat(),
                        auto_adjust=True,
                        progress=False
                    )
                    price_cache[yf_ibov] = ibov_data

                tk_data   = price_cache[yf_ticker]
                ibov_data = price_cache[yf_ibov]

                if tk_data.empty or ibov_data.empty or len(tk_data) < 3:
                    no_data += 1
                    self._update_event_status(conn, event_id, "NO_DATA", {})
                    continue

                # Calcula retornos diários
                tk_close   = tk_data["Close"].values.tolist()
                ibov_close = ibov_data["Close"].values.tolist()

                n = min(len(tk_close), len(ibov_close))
                tk_ret   = [(tk_close[i] - tk_close[i-1]) / tk_close[i-1] for i in range(1, n)]
                ibov_ret = [(ibov_close[i] - ibov_close[i-1]) / ibov_close[i-1] for i in range(1, n)]

                # Índice do evento (aprox. metade da série)
                event_idx = len(tk_ret) // 2

                # CAR em múltiplas janelas
                car_1 = _compute_car(tk_ret, ibov_ret, 0, 1, event_idx)
                car_3 = _compute_car(tk_ret, ibov_ret, 0, 3, event_idx)
                car_5 = _compute_car(tk_ret, ibov_ret, 0, 5, event_idx)
                car_sym = _compute_car(tk_ret, ibov_ret, -1, 1, event_idx)

                # p-value bootstrap na janela [0,+3]
                p_val = _bootstrap_pvalue(
                    car_3 if car_3 is not None else 0.0,
                    tk_ret, ibov_ret, n_days=3
                ) if car_3 is not None else 1.0

                reaction_data = {
                    "car_1d": round(car_1, 5) if car_1 is not None else None,
                    "car_3d": round(car_3, 5) if car_3 is not None else None,
                    "car_5d": round(car_5, 5) if car_5 is not None else None,
                    "car_symmetric_3d": round(car_sym, 5) if car_sym is not None else None,
                    "bootstrap_pvalue": round(p_val, 4),
                    "observations_used": n,
                    "validated_at": datetime.now(timezone.utc).isoformat()
                }

                # Classificação
                if car_3 is not None and abs(car_3) >= 0.01 and p_val <= 0.10:
                    classification = "CONFIRMED"
                    confirmed += 1
                elif car_3 is not None and abs(car_3) >= 0.005:
                    classification = "WEAK_SIGNAL"
                    weak += 1
                else:
                    classification = "NOISE"
                    noise += 1

                reaction_data["classification"] = classification
                self._update_event_status(conn, event_id, classification, reaction_data)
                validated += 1

            except Exception as e:
                no_data += 1
                self._update_event_status(conn, event_id, "VALIDATION_ERROR", {"error": str(e)[:200]})

        store.close()

        return {
            "total_evaluated": len(rows),
            "validated": validated,
            "confirmed": confirmed,
            "weak_signal": weak,
            "noise": noise,
            "no_data": no_data
        }

    def _update_event_status(self, conn, event_id: str, classification: str, data: dict) -> None:
        """Salva o resultado da validação de reação de mercado."""
        try:
            conn.execute("""
                INSERT OR REPLACE INTO market_reaction_results (
                    event_id, classification, reaction_json, validated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, [event_id, classification, json.dumps(data)])
        except Exception:
            # Tabela pode não existir ainda — cria e tenta novamente
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_reaction_results (
                    event_id VARCHAR PRIMARY KEY,
                    classification VARCHAR NOT NULL,
                    reaction_json VARCHAR,
                    validated_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO market_reaction_results (
                    event_id, classification, reaction_json, validated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, [event_id, classification, json.dumps(data)])
