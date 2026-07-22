from __future__ import annotations

import yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.ipe_models import IpeProcessingState
from macro_b3_bot.infrastructure.store import DatabaseStore

class IpePrioritizer:
    """
    Priorizador determinístico de documentos IPE da CVM (sem uso de LLM).
    Pontua a fila de processamento entre 0.0 e 1.0 com base em categoria, recência, vínculo com ticker e termos materiais.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

        config_yaml = Path(__file__).resolve().parent.parent.parent.parent / "config" / "ipe_categories.yaml"
        if config_yaml.exists():
            with open(config_yaml, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                self.category_weights = cfg.get("categories", {})
                self.material_terms = cfg.get("material_terms", [])
        else:
            self.category_weights = {"Fato Relevante": 1.0, "Comunicado ao Mercado": 0.85}
            self.material_terms = ["aquisição", "dividendos", "recuperação judicial"]

    def prioritize_queue(self, min_score_threshold: float = 0.65) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Carrega índice IPE e mapa de tickers
        docs = conn.execute("""
            SELECT i.document_id, i.cvm_code, i.category, i.subject, i.delivery_date, m.ticker
            FROM ipe_document_index i
            LEFT JOIN company_ticker_map m ON i.cvm_code = m.cvm_code AND m.validated = TRUE
        """).fetchall()

        now = datetime.now(timezone.utc)
        queued_count = 0
        high_priority_count = 0

        for doc in docs:
            doc_id, cvm_code, category, subject, deliv_dt, ticker = doc

            # 1. Peso Categoria (30%)
            cat_weight = self.category_weights.get(category, 0.30)

            # 2. Recência (25%)
            if deliv_dt.tzinfo is None:
                deliv_dt = deliv_dt.replace(tzinfo=timezone.utc)
            days_old = (now - deliv_dt).total_seconds() / 86400.0
            recency_score = max(0.0, 1.0 - (days_old / 365.0))

            # 3. Vínculo Ticker B3 (20%)
            ticker_score = 1.0 if ticker else 0.0

            # 4. Liquidez do Ativo (15%) - Padrão 0.5 se mapeado
            liquidity_score = 0.8 if ticker else 0.1

            # 5. Termos materiais (10%)
            subj_lower = (subject or "").lower()
            material_score = 1.0 if any(term in subj_lower for term in self.material_terms) else 0.0

            priority_score = round(
                (0.30 * cat_weight) +
                (0.25 * recency_score) +
                (0.20 * ticker_score) +
                (0.15 * liquidity_score) +
                (0.10 * material_score),
                4
            )

            status = "QUEUED" if priority_score >= min_score_threshold else "DISCOVERED"
            
            state = IpeProcessingState(
                document_id=doc_id,
                status=status,
                priority_score=priority_score,
                attempts=0,
                updated_at=now
            )

            store.save_ipe_processing_state(state.model_dump(mode="json"))
            queued_count += 1
            if priority_score >= min_score_threshold:
                high_priority_count += 1

        store.close()

        return {
            "total_processed": queued_count,
            "high_priority_queued": high_priority_count,
            "min_threshold": min_score_threshold
        }
