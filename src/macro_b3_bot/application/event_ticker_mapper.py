from __future__ import annotations

import logging
from typing import Dict, Any
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

# Mapeamento determinístico para o piloto/21 candidatos do Sprint 3A
PILOT_CVM_TO_TICKER = {
    "23531": "UNKNOWN",  # Claro Telecom Participações S.A.
    "26050": "FIQE3",    # UNIFIQUE TELECOMUNICAÇÕES S.A.
    "4669":  "GUAR3",    # GUARARAPES CONFECÇÕES SA
    "22608": "PGMN3",    # EMPREENDIMENTOS PAGUE MENOS SA
    "509280": "UNKNOWN", # PAGRISA - PARÁ PASTORIL E AGRÍCOLA S/A
    "19763": "ENBR3",    # EDP ENERGIAS DO BRASIL S/A (Fechou capital em 2023, mantemos para análise histórica)
    "20010": "EQTL3",    # EQUATORIAL S.A.
    "23000": "LIGT3",    # LIGHT ENERGIA S.A. (Mapeado para controladora listada)
    "25160": "SEQL3",    # SEQUOIA LOGÍSTICA E TRANSPORTES S.A.
    "27049": "ORVR3",    # ORIZON MEIO AMBIENTE S.A.
    "25780": "RECV3",    # PETRORECÔNCAVO S.A.
    "26166": "UNKNOWN",  # GRUPO FARTURA DE HORTIFRUT S.A. (Oba Hortifruti)
    "21016": "YDUQ3",    # YDUQS PARTICIPACOES S.A.
    "22810": "UNKNOWN",  # ELDORADO BRASIL CELULOSE S.A.
    "23167": "UNKNOWN",  # RODOVIAS DAS COLINAS S.A.
    "24821": "RDOR3",    # REDE D'OR SÃO LUIZ S.A.
}

class EventTickerMapper:
    """
    Mapeia os cvm_codes dos EventCandidates para tickers negociados na B3.
    Salva os resultados em company_ticker_map e event_market_mappings,
    e atualiza a tabela event_candidates.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def run_mapping(self) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # 1. Carrega todos os EventCandidates
        candidates = conn.execute(
            "SELECT event_id, cvm_code, ticker, event_type FROM event_candidates"
        ).fetchall()

        mapped_count = 0
        unknown_count = 0
        total = len(candidates)

        for event_id, cvm_code, current_ticker, event_type in candidates:
            # Resolve o ticker
            ticker = PILOT_CVM_TO_TICKER.get(cvm_code)
            mapping_source = "STATIC_TABLE"
            confidence = 1.0

            if not ticker:
                # Tenta buscar na tabela de companhias da CVM se houver
                company = conn.execute(
                    "SELECT legal_name, cnpj FROM cvm_companies WHERE cvm_code = ?",
                    [cvm_code]
                ).fetchone()
                if company:
                    legal_name, cnpj = company
                    # Fuzzy match simples contra os snapshots disponíveis da B3
                    match = conn.execute(
                        """
                        SELECT ticker FROM asset_snapshots
                        WHERE LOWER(sector) LIKE ? OR LOWER(ticker) LIKE ?
                        LIMIT 1
                        """,
                        [f"%{legal_name[:10].lower()}%", f"%{legal_name[:4].lower()}%"]
                    ).fetchone()
                    if match:
                        ticker = match[0]
                        mapping_source = "NAME_FUZZY"
                        confidence = 0.7
                    else:
                        ticker = "UNKNOWN"
                        mapping_source = "NAME_FUZZY"
                        confidence = 0.0
                else:
                    ticker = "UNKNOWN"
                    mapping_source = "UNKNOWN"
                    confidence = 0.0

            # Obtém CNPJ da companhia se disponível
            cnpj = conn.execute(
                "SELECT cnpj FROM cvm_companies WHERE cvm_code = ?",
                [cvm_code]
            ).fetchone()
            cnpj_str = cnpj[0] if cnpj else "00000000000000"

            # Salva na tabela company_ticker_map se for um ticker conhecido
            if ticker != "UNKNOWN":
                store.save_ticker_mapping({
                    "ticker": ticker,
                    "cvm_code": cvm_code,
                    "cnpj": cnpj_str,
                    "mapping_source": mapping_source,
                    "confidence": confidence,
                    "validated": True
                })
                mapped_count += 1
            else:
                unknown_count += 1

            # Cria e salva o EventMarketMapping no banco
            market_symbol = f"{ticker}.SA" if ticker != "UNKNOWN" else "UNKNOWN"
            asset_class = "STOCK"
            # Se fosse FII/ETF/BDR poderíamos diferenciar. Pro piloto, os mapeados são STOCK.
            
            mapping = {
                "event_id": event_id,
                "cvm_code": cvm_code,
                "primary_ticker": ticker,
                "related_tickers": [],
                "market_symbol": market_symbol,
                "asset_class": asset_class,
                "mapping_confidence": confidence,
                "mapping_source": mapping_source,
                "validated": True if ticker != "UNKNOWN" else False
            }
            store.save_event_market_mapping(mapping)

            # Atualiza o ticker e status na tabela event_candidates
            conn.execute(
                "UPDATE event_candidates SET ticker = ? WHERE event_id = ?",
                [ticker, event_id]
            )

        store.close()
        logger.info(f"Mapeamento concluído: {mapped_count} mapeados, {unknown_count} desconhecidos.")
        return {
            "total_processed": total,
            "mapped": mapped_count,
            "unknown": unknown_count
        }
