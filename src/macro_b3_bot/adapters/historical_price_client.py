from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Protocol, Optional
import pandas as pd
from macro_b3_bot.domain.event_study_models import MarketPrice

logger = logging.getLogger(__name__)

class HistoricalMarketDataProvider(Protocol):
    """
    Interface para provedores de dados históricos de mercado.
    """
    def fetch_prices(
        self,
        ticker: str,
        start_date: date,
        end_date: date
    ) -> List[MarketPrice]:
        ...

class YahooFinanceProvider:
    """
    Adapter para coletar dados históricos de preços usando yfinance.
    """
    def __init__(self):
        try:
            import yfinance as yf
            self.yf = yf
        except ImportError as e:
            logger.error("yfinance não instalado. Execute: pip install yfinance")
            raise e

    def fetch_prices(
        self,
        ticker: str,
        start_date: date,
        end_date: date
    ) -> List[MarketPrice]:
        HISTORICAL_TO_CURRENT_TICKER = {
            "GUAR3": "RIAA3"
        }
        download_ticker = HISTORICAL_TO_CURRENT_TICKER.get(ticker, ticker)
        yf_ticker = download_ticker
        if not yf_ticker.startswith("^") and "." not in yf_ticker:
            yf_ticker = f"{yf_ticker}.SA"

        logger.info(f"Baixando precos históricos para {yf_ticker} de {start_date} ate {end_date}")
        
        try:
            # Download sem ajuste automatico para termos o close e o adjusted_close originais
            df = self.yf.download(
                yf_ticker,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                auto_adjust=False,
                progress=False
            )
            
            if df.empty:
                logger.warning(f"Nenhum dado retornado para o ticker {yf_ticker}")
                return []

            prices: List[MarketPrice] = []
            collected_at = datetime.now(timezone.utc)

            # yfinance pode retornar colunas multi-index se baixado em lote ou single index
            # Vamos garantir o tratamento de colunas planas
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            for idx, row in df.iterrows():
                # idx é o Timestamp correspondente à data de negociação
                trading_date = idx.date() if hasattr(idx, "date") else idx
                
                # yfinance às vezes retorna valores ausentes
                close_val = row.get("Close")
                if pd.isna(close_val):
                    continue

                open_val = row.get("Open")
                high_val = row.get("High")
                low_val = row.get("Low")
                adj_close = row.get("Adj Close")
                vol_val = row.get("Volume")

                prices.append(MarketPrice(
                    ticker=ticker,
                    trading_date=trading_date,
                    open=Decimal(str(open_val)) if not pd.isna(open_val) else None,
                    high=Decimal(str(high_val)) if not pd.isna(high_val) else None,
                    low=Decimal(str(low_val)) if not pd.isna(low_val) else None,
                    close=Decimal(str(close_val)),
                    adjusted_close=Decimal(str(adj_close)) if not pd.isna(adj_close) else None,
                    volume=Decimal(str(vol_val)) if not pd.isna(vol_val) else None,
                    source="yahoo_finance",
                    collected_at=collected_at
                ))
            
            logger.info(f"Sucesso: {len(prices)} registros obtidos para {ticker}")
            return prices

        except Exception as e:
            logger.error(f"Erro ao obter dados para {yf_ticker}: {e}")
            return []
