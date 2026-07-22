"""
Sprint 3A: Build Evidence - Versão calibrada com padrões reais dos IPEs CVM.

Padrões observados nos documentos:
- "R$ 0,54321 por ação" (dividendo)  
- "R$ 167.698.667,00" (valor total de JCP)
- "recompra de até N ações"
- valores como "R$0,12345" sem espaço
- "por ação ordinária" / "por cota" (FII)
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone
from typing import Dict, Any, List, Optional

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.evidence_models import EvidenceClaim
from macro_b3_bot.infrastructure.store import DatabaseStore


# ------------------------------------------------------------------ #
# REGEX CALIBRADOS PARA PADRÕES REAIS CVM
# ------------------------------------------------------------------ #

# R$ 0,54321 por ação | R$0,54 por cota | R$ 1.234,56 por ação ordinária
RE_VALUE_PER_SHARE = re.compile(
    r"R\$\s*([\d\.]+,[\d]{2,5})\s*(?:por\s+(?:ação(?:\s+(?:ordinária|preferencial))?|cota|papel|unit))",
    re.IGNORECASE
)

# "dividendo" ou "JCP" perto de valor monetário (janela de 400 chars)
RE_DIVIDEND_CONTEXT = re.compile(
    r"(dividendos?|juros\s+sobre\s+(?:o\s+)?capital\s+próprio|jcp)",
    re.IGNORECASE
)

# valor total: R$ 167.698.667,00 (para JCP/dividendo de valor total)
RE_TOTAL_VALUE = re.compile(
    r"R\$\s*([\d\.]+,[\d]{2})",
    re.IGNORECASE
)

# recompra de ações
RE_BUYBACK = re.compile(
    r"(programa\s+de\s+recompra|recomprar|recompra\s+de\s+até)\s+(?:até\s+)?([0-9\.]+)\s*(?:de\s+)?(?:ações|papéis|units)",
    re.IGNORECASE
)

# data ex-dividendo
RE_EX_DATE = re.compile(
    r"(?:data\s+de\s+)?(?:ex[- ])?(?:dividendo|jcp|provento)[:\s]+(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE
)

# bonificação
RE_BONUS = re.compile(
    r"bonifica(?:ção|r)[^.]*?(\d+)\s*(?:novas?\s+)?ações\s+para\s+(?:cada|todo[s]?\s+)?(\d+)",
    re.IGNORECASE
)

# aumento de capital
RE_CAPITAL_INCREASE = re.compile(
    r"aumento\s+(?:do\s+)?capital\s+social[^.]*?R\$\s*([\d\.]+,[\d]{2})",
    re.IGNORECASE
)

# emissão de debêntures / notas comerciais
RE_DEBT = re.compile(
    r"(?:emissão\s+de|emitir)[^.]*?(?:debêntures?|notas?\s+comerciais?|CRA|CRI)[^.]*?R\$\s*([\d\.]+,[\d]{2})",
    re.IGNORECASE
)


def _parse_brl(raw: str) -> Optional[Decimal]:
    """Converte '1.234,56' → Decimal('1234.56')"""
    try:
        cleaned = raw.replace(".", "").replace(",", ".")
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date(raw: str) -> Optional[date]:
    """Converte '31/12/2025' ou '2025-12-31' para date"""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


class IpeEvidenceBuilder:
    """
    Construtor determinístico de EvidenceClaims a partir de documentos IPE.
    Padrões calibrados com documentos reais da CVM.
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
            LEFT JOIN company_ticker_map m ON i.cvm_code = m.cvm_code
            WHERE e.extraction_quality >= 0.50
            LIMIT ?
        """, [limit]).fetchall()

        claims_count = 0

        for doc_id, cvm_code, ticker, category, subject, text in rows:
            extracted = self._extract_claims(
                doc_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                subject=subject or category or "",
                text=text
            )

            for claim in extracted:
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

    def _extract_claims(
        self,
        doc_id: str,
        cvm_code: str,
        ticker: Optional[str],
        subject: str,
        text: str
    ) -> List[EvidenceClaim]:
        claims: List[EvidenceClaim] = []
        now = datetime.now(timezone.utc)

        # ----------------------------------------------------------
        # 1. Valor por ação/cota (dividendo, JCP)
        # ----------------------------------------------------------
        for val_match in RE_VALUE_PER_SHARE.finditer(text):
            val = _parse_brl(val_match.group(1))
            if val is None or val <= 0:
                continue

            # Verifica contexto: busca tipo (DIVIDEND / JCP) em janela de ±500 chars
            start = max(0, val_match.start() - 500)
            end = min(len(text), val_match.end() + 200)
            ctx = text[start:end]

            type_match = RE_DIVIDEND_CONTEXT.search(ctx)
            claim_type = "DIVIDEND"
            predicate  = "HAS_DIVIDEND_PER_SHARE"
            if type_match and ("jcp" in type_match.group(1).lower() or "juros" in type_match.group(1).lower()):
                claim_type = "JCP"
                predicate  = "HAS_JCP_PER_SHARE"

            # Data ex-div se disponível
            effective_dt: Optional[date] = None
            date_m = RE_EX_DATE.search(ctx)
            if date_m:
                effective_dt = _parse_date(date_m.group(1))

            excerpt = val_match.group(0)[:300]
            claims.append(EvidenceClaim(
                claim_id=f"CLAIM_{uuid.uuid4().hex[:12]}",
                document_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                claim_type=claim_type,
                subject=subject,
                predicate=predicate,
                object_text=f"{claim_type} de R$ {val} por ação/cota",
                numeric_value=val,
                unit="BRL_PER_SHARE",
                currency="BRL",
                effective_date=effective_dt,
                source_page=1,
                source_start=val_match.start(),
                source_end=val_match.end(),
                source_excerpt=excerpt,
                extraction_method="REGEX_CALIBRATED_V2",
                confidence=0.92,
                created_at=now
            ))

        # ----------------------------------------------------------
        # 2. Recompra de ações
        # ----------------------------------------------------------
        for m in RE_BUYBACK.finditer(text):
            qty_raw = m.group(2).replace(".", "")
            try:
                qty = Decimal(qty_raw)
            except InvalidOperation:
                continue
            if qty <= 0:
                continue

            claims.append(EvidenceClaim(
                claim_id=f"CLAIM_{uuid.uuid4().hex[:12]}",
                document_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                claim_type="SHARE_BUYBACK",
                subject=subject,
                predicate="HAS_BUYBACK_SHARES_QUANTITY",
                object_text=f"Recompra de até {qty} ações",
                numeric_value=qty,
                unit="SHARES",
                currency=None,
                source_page=1,
                source_start=m.start(),
                source_end=m.end(),
                source_excerpt=m.group(0)[:300],
                extraction_method="REGEX_CALIBRATED_V2",
                confidence=0.88,
                created_at=now
            ))

        # ----------------------------------------------------------
        # 3. Aumento de capital
        # ----------------------------------------------------------
        for m in RE_CAPITAL_INCREASE.finditer(text):
            val = _parse_brl(m.group(1))
            if val is None or val <= 0:
                continue

            claims.append(EvidenceClaim(
                claim_id=f"CLAIM_{uuid.uuid4().hex[:12]}",
                document_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                claim_type="CAPITAL_INCREASE",
                subject=subject,
                predicate="HAS_CAPITAL_INCREASE_VALUE",
                object_text=f"Aumento de capital de R$ {val}",
                numeric_value=val,
                unit="BRL",
                currency="BRL",
                source_page=1,
                source_start=m.start(),
                source_end=m.end(),
                source_excerpt=m.group(0)[:300],
                extraction_method="REGEX_CALIBRATED_V2",
                confidence=0.85,
                created_at=now
            ))

        # ----------------------------------------------------------
        # 4. Emissão de dívida (debêntures / notas comerciais)
        # ----------------------------------------------------------
        for m in RE_DEBT.finditer(text):
            val = _parse_brl(m.group(1))
            if val is None or val <= 0:
                continue

            claims.append(EvidenceClaim(
                claim_id=f"CLAIM_{uuid.uuid4().hex[:12]}",
                document_id=doc_id,
                cvm_code=cvm_code,
                ticker=ticker,
                claim_type="DEBT_ISSUANCE",
                subject=subject,
                predicate="HAS_DEBT_ISSUANCE_VALUE",
                object_text=f"Emissão de dívida R$ {val}",
                numeric_value=val,
                unit="BRL",
                currency="BRL",
                source_page=1,
                source_start=m.start(),
                source_end=m.end(),
                source_excerpt=m.group(0)[:300],
                extraction_method="REGEX_CALIBRATED_V2",
                confidence=0.82,
                created_at=now
            ))

        return claims
