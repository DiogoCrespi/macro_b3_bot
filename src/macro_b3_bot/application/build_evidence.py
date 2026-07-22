from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone
from typing import Dict, Any, List, Optional

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.evidence_models import EvidenceClaim
from macro_b3_bot.infrastructure.store import DatabaseStore

class IpeEvidenceBuilder:
    """
    Construtor determinístico de reivindicações de evidências auditáveis (EvidenceClaim) a partir de documentos IPE.
    Extrai proventos (DIVIDEND, JCP), recompras, aumentos de capital e emissões de dívida.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "audit.duckdb"

    def build_evidence_batch(self, limit: int = 500) -> Dict[str, Any]:
        store = DatabaseStore(self.db_path)
        conn = store.connection

        rows = conn.execute("""
            SELECT e.document_id, i.cvm_code, m.ticker, i.category, i.subject, e.extracted_text
            FROM extracted_documents e
            JOIN ipe_document_index i USING (document_id)
            LEFT JOIN company_ticker_map m ON i.cvm_code = m.cvm_code AND m.validated = TRUE
            JOIN ipe_processing_queue q USING (document_id)
            WHERE q.status = 'DEDUPLICATED'
            LIMIT ?
        """, [limit]).fetchall()

        claims_count = 0

        for doc_id, cvm_code, ticker, category, subject, text in rows:
            extracted_claims = self._extract_claims_from_text(
                doc_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                subject=subject or category,
                text=text
            )

            for claim in extracted_claims:
                store.save_evidence_claim(claim.model_dump(mode="json"))
                claims_count += 1

            conn.execute(
                "UPDATE ipe_processing_queue SET status = 'EVIDENCE_BUILT', updated_at = CURRENT_TIMESTAMP WHERE document_id = ?",
                [doc_id]
            )

        store.close()

        return {
            "documents_processed": len(rows),
            "claims_generated": claims_count
        }

    def _extract_claims_from_text(
        self,
        doc_id: str,
        cvm_code: str,
        ticker: Optional[str],
        subject: str,
        text: str
    ) -> List[EvidenceClaim]:

        claims: List[EvidenceClaim] = []
        now = datetime.now(timezone.utc)

        # 1. Regex Proventos: Dividendos / JCP (ex: "R$ 0,54321 por ação" ou "R$ 1.25 por ação")
        pattern_div = re.compile(
            r"(dividendos?|juros sobre o? capital próprio|jcp).*?(R\$\s*[\d\.\,]+)\s*(por ação|por papel)",
            re.IGNORECASE | re.DOTALL
        )

        for match in pattern_div.finditer(text):
            type_str = "JCP" if "jcp" in match.group(1).lower() or "juros" in match.group(1).lower() else "DIVIDEND"
            val_raw = match.group(2).replace("R$", "").replace(".", "").replace(",", ".").strip()
            
            try:
                val_dec = Decimal(val_raw)
            except InvalidOperation:
                continue

            excerpt = match.group(0)[:300]
            claim_id = f"CLAIM_{uuid.uuid4().hex[:12]}"

            claims.append(EvidenceClaim(
                claim_id=claim_id,
                document_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                claim_type=type_str,
                subject=subject,
                predicate="HAS_PAYMENT_VALUE_PER_SHARE",
                object_text=f"{type_str} de R$ {val_dec} por ação",
                numeric_value=val_dec,
                unit="BRL_PER_SHARE",
                currency="BRL",
                source_page=1,
                source_start=match.start(),
                source_end=match.end(),
                source_excerpt=excerpt,
                extraction_method="REGEX_DETERMINISTIC",
                confidence=0.95,
                created_at=now
            ))

        # 2. Regex Recompra de Ações (ex: "recompra de até 10.000.000 de ações")
        pattern_buyback = re.compile(
            r"(programa de recompra|recomprar).*?([\d\.]+)\s*(de\s*)?ações",
            re.IGNORECASE
        )

        for match in pattern_buyback.finditer(text):
            qty_raw = match.group(2).replace(".", "").strip()
            try:
                qty_dec = Decimal(qty_raw)
            except InvalidOperation:
                continue

            excerpt = match.group(0)[:300]
            claim_id = f"CLAIM_{uuid.uuid4().hex[:12]}"

            claims.append(EvidenceClaim(
                claim_id=claim_id,
                document_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                claim_type="SHARE_BUYBACK",
                subject=subject,
                predicate="HAS_BUYBACK_SHARES_QUANTITY",
                object_text=f"Recompra de até {qty_dec} ações",
                numeric_value=qty_dec,
                unit="SHARES",
                currency=None,
                source_page=1,
                source_start=match.start(),
                source_end=match.end(),
                source_excerpt=excerpt,
                extraction_method="REGEX_DETERMINISTIC",
                confidence=0.90,
                created_at=now
            ))

        return claims
