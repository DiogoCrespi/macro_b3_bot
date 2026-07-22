from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, Any
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

class EventReactionsAuditor:
    """
    Exporta relatórios de auditoria e tabelas de resultados em formato CSV:
    - data/audits/evidence_claims_audit.csv (41 claims com colunas de controle manual)
    - data/audits/event_market_outcomes.csv (resultados quantitativos e classificação do event study)
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"
        self.output_dir = settings.data_dir / "audits"

    def run_export(self) -> Dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # 1. Exporta Claims Audit
        claims = conn.execute(
            """
            SELECT claim_id, ticker, claim_type, numeric_value, unit,
                   source_excerpt, document_id, source_page
            FROM evidence_claims
            ORDER BY claim_id ASC
            """
        ).fetchall()

        claims_file = self.output_dir / "evidence_claims_audit.csv"
        with open(claims_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "claim_id", "ticker", "claim_type", "numeric_value", "unit",
                "source_excerpt", "document_id", "source_page", "human_valid",
                "human_type_correct", "human_value_correct", "human_ticker_correct", "review_notes"
            ])
            for r in claims:
                writer.writerow(list(r) + ["", "", "", "", ""])

        # 2. Exporta Outcomes Audit
        outcomes = conn.execute(
            """
            SELECT event_id, ticker, publication_timestamp, effective_trading_date, publication_session,
                   prior_close, raw_return_1d, raw_return_5d, raw_return_20d, raw_return_60d,
                   car_1d, car_5d, car_20d, car_60d, pre_event_car_5d, event_window_car,
                   beta, historical_volatility, volume_zscore,
                   bootstrap_pvalue_1d, bootstrap_pvalue_5d, bootstrap_pvalue_20d, outcome_label, calculated_at
            FROM event_market_outcomes
            ORDER BY outcome_label DESC, car_5d DESC
            """
        ).fetchall()

        outcomes_file = self.output_dir / "event_market_outcomes.csv"
        with open(outcomes_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "event_id", "ticker", "publication_timestamp", "effective_trading_date", "publication_session",
                "prior_close", "raw_return_1d", "raw_return_5d", "raw_return_20d", "raw_return_60d",
                "car_1d", "car_5d", "car_20d", "car_60d", "pre_event_car_5d", "event_window_car",
                "beta", "historical_volatility", "volume_zscore",
                "bootstrap_pvalue_1d", "bootstrap_pvalue_5d", "bootstrap_pvalue_20d", "outcome_label", "calculated_at"
            ])
            for r in outcomes:
                writer.writerow(r)

        store.close()
        logger.info(f"Relatorios salvos em {self.output_dir}")
        return {
            "claims_exported": len(claims),
            "outcomes_exported": len(outcomes),
            "claims_path": str(claims_file),
            "outcomes_path": str(outcomes_file)
        }
