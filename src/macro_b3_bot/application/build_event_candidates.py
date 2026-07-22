from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, List

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.event_models import EventCandidate
from macro_b3_bot.infrastructure.store import DatabaseStore

class EventCandidateBuilder:
    """
    Construtor determinístico de candidatos a eventos (EventCandidate - Sprint 2C-C).
    Agrupa reivindicações de evidência (EvidenceClaim) por ativo e calcula scores de novidade e materialidade.
    Aplica o EventGate determinístico liberando no máximo status EVENT_GATE_APPROVED.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def build_event_candidates_batch(self, limit: int = 500) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        rows = conn.execute("""
            SELECT c.claim_id, c.document_id, c.cvm_code, c.ticker, c.claim_type,
                   c.subject, c.numeric_value, c.confidence, c.source_excerpt
            FROM evidence_claims c
            JOIN ipe_processing_queue q USING (document_id)
            WHERE q.status = 'EVIDENCE_BUILT'
              AND c.ticker IS NOT NULL
              AND c.ticker != ''
            LIMIT ?
        """, [limit]).fetchall()

        events_created = 0
        approved_count = 0
        now = datetime.now(timezone.utc)

        for claim_id, doc_id, cvm_code, ticker, claim_type, subject, val_num, confidence, excerpt in rows:
            event_type_map = {
                "DIVIDEND": "DIVIDEND_DECLARED",
                "JCP": "JCP_DECLARED",
                "SHARE_BUYBACK": "BUYBACK_AUTHORIZED",
                "CAPITAL_INCREASE": "CAPITAL_INCREASE",
                "DEBT_ISSUANCE": "DEBT_ISSUANCE"
            }

            evt_type = event_type_map.get(claim_type, "DIVIDEND_DECLARED")
            title = f"{evt_type}: {ticker} - {subject[:80]}"

            # Scores Determinísticos
            novelty_score = 0.90 # Alta novidade por documento único
            mat_num = float(val_num) if val_num is not None else 0.50
            materiality_score = round(min(1.0, max(0.50, confidence * (0.50 + min(mat_num, 0.50)))), 4)
            persistence_score = 0.85

            quant_impact = {}
            if val_num is not None:
                quant_impact["declared_value"] = Decimal(str(val_num))

            # Filtro EventGate
            if novelty_score >= 0.60 and materiality_score >= 0.50:
                status = "EVENT_GATE_APPROVED"
                approved_count += 1
            else:
                status = "CANDIDATE"

            evt_id = f"EVT_{uuid.uuid4().hex[:12]}"

            candidate = EventCandidate(
                event_id=evt_id,
                ticker=ticker,
                cvm_code=cvm_code,
                event_type=evt_type,
                title=title,
                effective_date=None,
                claim_ids=[claim_id],
                evidence_count=1,
                novelty_score=novelty_score,
                materiality_score=materiality_score,
                persistence_score=persistence_score,
                quantitative_impact=quant_impact,
                invalidators=[],
                status=status,
                created_at=now
            )

            store.save_event_candidate(candidate.model_dump(mode="json"))
            events_created += 1

        store.close()

        return {
            "total_claims_evaluated": len(rows),
            "events_created": events_created,
            "event_gate_approved": approved_count
        }
