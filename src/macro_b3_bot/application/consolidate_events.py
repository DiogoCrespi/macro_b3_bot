"""
Sprint 3A: Consolidação de EvidenceClaims → EventCandidates

Regras:
- 1 claim de dividendo/JCP com valor > 0 → DIVIDEND_DECLARED / JCP_DECLARED
- 1 claim de recompra com qty > 0 → BUYBACK_AUTHORIZED
- Múltiplos claims do mesmo tipo/empresa → consolida em 1 evento
- Score de novelty: 1.0 para primeiro do trimestre, 0.5 para reapresentação
- Score de materiality: baseia no valor relativo ao preço da ação (quando disponível)
"""
from __future__ import annotations

import uuid
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List, Optional

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


class EventConsolidator:
    """
    Consolida EvidenceClaims em EventCandidates com scores de novelty e materiality.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def consolidate(self, limit: int = 2000) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        # Carrega claims não processados ainda com o timestamp de publicação (delivery_date) do documento correspondente
        rows = conn.execute("""
            SELECT
                c.claim_id,
                c.document_id,
                c.cvm_code,
                c.ticker,
                c.claim_type,
                c.subject,
                c.object_text,
                c.numeric_value,
                c.currency,
                c.effective_date,
                c.confidence,
                c.created_at,
                idx.delivery_date as publication_timestamp
            FROM evidence_claims c
            JOIN ipe_document_index idx ON c.document_id = idx.document_id
            WHERE NOT EXISTS (
                SELECT 1 FROM event_candidates ec
                WHERE ec.claim_ids LIKE '%' || c.claim_id || '%'
            )
              AND c.document_id NOT IN (
                  SELECT duplicate_document_id FROM document_duplicate_links
              )
            ORDER BY c.cvm_code, c.created_at
            LIMIT ?
        """, [limit]).fetchall()

        # Agrupa por evento econômico (cvm_code, data de anúncio)
        groups: Dict[str, List[dict]] = {}
        for row in rows:
            (claim_id, doc_id, cvm_code, ticker, claim_type,
             subject, object_text, numeric_value, currency,
             effective_date, confidence, created_at, pub_ts) = row

            if pub_ts is not None:
                if isinstance(pub_ts, str):
                    try:
                        dt = datetime.fromisoformat(pub_ts)
                    except ValueError:
                        dt = datetime.strptime(pub_ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    announcement_date = dt.date().isoformat()
                else:
                    announcement_date = pub_ts.date().isoformat()
            else:
                announcement_date = "UNKNOWN"

            key = f"{cvm_code}|{announcement_date}"
            if key not in groups:
                groups[key] = []
            groups[key].append({
                "claim_id": claim_id,
                "document_id": doc_id,
                "cvm_code": cvm_code,
                "ticker": ticker,
                "claim_type": claim_type,
                "subject": subject,
                "object_text": object_text,
                "numeric_value": numeric_value,
                "currency": currency,
                "effective_date": effective_date,
                "confidence": confidence,
                "created_at": created_at,
                "publication_timestamp": pub_ts,
                "announcement_date": announcement_date
            })

        CLAIM_TO_EVENT_TYPE = {
            "DIVIDEND": "DIVIDEND_DECLARED",
            "JCP": "JCP_DECLARED",
            "SHARE_BUYBACK": "BUYBACK_AUTHORIZED",
            "CAPITAL_INCREASE": "CAPITAL_INCREASE",
            "DEBT_ISSUANCE": "DEBT_ISSUANCE",
        }

        # Prioridade para seleção do tipo do evento dominante
        CLAIM_PRIORITY = {
            "DIVIDEND": 1,
            "JCP": 2,
            "CAPITAL_INCREASE": 3,
            "DEBT_ISSUANCE": 4,
            "SHARE_BUYBACK": 5
        }

        events_created = 0
        now = datetime.now(timezone.utc)

        for key, claims in groups.items():
            cvm_code = claims[0]["cvm_code"]
            ticker = claims[0]["ticker"] or cvm_code
            announcement_date = claims[0]["announcement_date"]

            # Ordena claims pela prioridade do tipo
            sorted_claims = sorted(claims, key=lambda x: CLAIM_PRIORITY.get(x["claim_type"], 99))
            primary_claim = sorted_claims[0]
            primary_claim_type = primary_claim["claim_type"]
            event_type = CLAIM_TO_EVENT_TYPE.get(primary_claim_type)
            if not event_type:
                continue

            # Valor representativo é o valor do claim principal
            rep_value = primary_claim["numeric_value"]

            claim_ids = [c["claim_id"] for c in claims]
            avg_confidence = sum(c["confidence"] for c in claims) / len(claims)

            # Novelty: 1.0 se é o único evento desse tipo para essa empresa, 0.5 se já existe
            existing_count = conn.execute("""
                SELECT COUNT(*) FROM event_candidates
                WHERE cvm_code = ? AND event_type = ?
            """, [cvm_code, event_type]).fetchone()[0]

            novelty_score = 1.0 if existing_count == 0 else 0.5

            # Materiality: baseada no confidence médio e número de claims
            evidence_weight = min(1.0, len(claims) / 3.0)
            materiality_score = round(avg_confidence * 0.7 + evidence_weight * 0.3, 3)

            # Valor monetário para quantitative_impact
            quant_impact = {}
            if rep_value is not None:
                unit = primary_claim.get("currency", "BRL") or "BRL"
                quant_impact[unit] = float(rep_value)

            candidate = {
                "event_id": f"EVT_{uuid.uuid4().hex[:12]}",
                "ticker": ticker,
                "cvm_code": cvm_code,
                "event_type": event_type,
                "title": f"{event_type.replace('_', ' ')} — {ticker} ({len(claims)} claims)",
                "effective_date": announcement_date,
                "claim_ids": claim_ids,
                "evidence_count": len(claims),
                "novelty_score": novelty_score,
                "materiality_score": materiality_score,
                "persistence_score": 0.8,
                "quantitative_impact": quant_impact,
                "invalidators": [],
                "publication_timestamp": primary_claim["publication_timestamp"],
                "status": "CANDIDATE",
            }

            store.save_event_candidate(candidate)
            events_created += 1

        store.close()

        return {
            "claims_evaluated": len(rows),
            "groups_formed": len(groups),
            "events_created": events_created
        }
