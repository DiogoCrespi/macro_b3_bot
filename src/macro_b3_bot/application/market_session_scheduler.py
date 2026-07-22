from __future__ import annotations

import logging
from datetime import datetime, date, time
from typing import List, Optional
import zoneinfo
from macro_b3_bot.domain.event_study_models import EffectiveMarketEvent

logger = logging.getLogger(__name__)

class MarketSessionScheduler:
    """
    Classifica o timestamp de publicação de um fato relevante no fuso de São Paulo
    e mapeia para a data de pregão efetiva na B3.
    """
    def __init__(self, tz_name: str = "America/Sao_Paulo"):
        self.tz = zoneinfo.ZoneInfo(tz_name)

    def compute_effective_dates(
        self,
        event_id: str,
        pub_timestamp: datetime,
        trading_dates: List[date]
    ) -> EffectiveMarketEvent:
        """
        Calcula as datas efetivas B3 com base nos pregões reais ocorridos (extraídos do benchmark).
        """
        # Garante ordenação crescente e unicidade
        sorted_dates = sorted(list(set(trading_dates)))
        if not sorted_dates:
            raise ValueError("Lista de datas de pregao vazia")

        # Converte para o fuso local de São Paulo
        if pub_timestamp.tzinfo is None:
            # Se for exatamente meia-noite (00:00:00), assume que já é no fuso de São Paulo
            # para evitar que o offset UTC-3 desloque a data para o dia anterior (ex: domingo)
            if pub_timestamp.time() == time(0, 0):
                pub_local = pub_timestamp.replace(tzinfo=self.tz)
            else:
                pub_local = pub_timestamp.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(self.tz)
        else:
            pub_local = pub_timestamp.astimezone(self.tz)

        pub_date = pub_local.date()
        pub_time = pub_local.time()

        is_trading_day = pub_date in sorted_dates

        if not is_trading_day:
            session = "NON_TRADING_DAY"
            # O pregão efetivo é o primeiro dia de negociação após a publicação
            effective_date = self._find_first_after(pub_date, sorted_dates)
            prev_date = self._find_last_before(pub_date, sorted_dates)
            first_full_date = effective_date
        else:
            # Dia útil e dia de negociação
            if pub_time < time(10, 0):
                session = "PRE_MARKET"
                effective_date = pub_date
                prev_date = self._find_last_before(pub_date, sorted_dates)
                first_full_date = pub_date
            elif pub_time <= time(18, 0):
                session = "INTRADAY"
                effective_date = pub_date
                prev_date = self._find_last_before(pub_date, sorted_dates)
                # O primeiro dia com pregão completo após a notícia intraday é o dia seguinte útil
                first_full_date = self._find_first_after(pub_date, sorted_dates)
            else:
                session = "POST_MARKET"
                # Pregão efetivo é o próximo dia útil
                effective_date = self._find_first_after(pub_date, sorted_dates)
                prev_date = pub_date
                first_full_date = effective_date

        return EffectiveMarketEvent(
            event_id=event_id,
            publication_timestamp=pub_local,
            publication_tz=self.tz.key,
            publication_session=session,
            previous_trading_date=prev_date,
            effective_trading_date=effective_date,
            first_full_trading_date=first_full_date
        )

    def _find_first_after(self, target: date, dates: List[date]) -> date:
        for d in dates:
            if d > target:
                return d
        # Se não achou (ex: fim do histórico), projeta o próximo dia útil
        logger.warning(f"Data posterior a {target} nao encontrada no historico. Projetando...")
        return self._project_next_weekday(target)

    def _find_last_before(self, target: date, dates: List[date]) -> Optional[date]:
        for d in reversed(dates):
            if d < target:
                return d
        return None

    def _project_next_weekday(self, target: date) -> date:
        from datetime import timedelta
        candidate = target + timedelta(days=1)
        while candidate.weekday() >= 5:  # Sábado (5) ou Domingo (6)
            candidate += timedelta(days=1)
        return candidate
