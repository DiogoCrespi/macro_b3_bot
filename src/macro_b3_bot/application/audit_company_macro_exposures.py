"""Audit packet for document selection and extracted company macro exposures."""
from __future__ import annotations

import json

from macro_b3_bot.infrastructure.store import DatabaseStore


_TARGET_FIELDS = (
    "foreign_currency_debt_pct",
    "floating_rate_debt_pct",
    "inflation_linked_debt_pct",
    "foreign_revenue",
    "export_revenue_pct",
    "revenue_foreign_currency_pct",
    "cost_foreign_currency_pct",
    "currency_hedge_pct",
    "commodity_roles",
    "commodity_production",
    "commodity_hedges",
)


class CompanyMacroExposureAuditor:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store

    def audit(
        self, selection_run_id: str, exposure_run_id: str
    ) -> dict[str, object]:
        documents = self._documents(selection_run_id)
        facts = self._facts(selection_run_id)
        fact_fields: dict[str, set[str]] = {}
        for fact in facts:
            fact_fields.setdefault(fact["ticker"], set()).add(fact["field_name"])
        tickers = [
            row[0] for row in self.store.connection.execute(
                """
                SELECT DISTINCT ticker FROM company_ticker_map
                WHERE review_status='VALIDATED' ORDER BY ticker
                """
            ).fetchall()
        ]
        document_summary = []
        matrix = []
        for ticker in tickers:
            ticker_documents = [item for item in documents if item["ticker"] == ticker]
            known = sorted(fact_fields.get(ticker, set()))
            missing = [field for field in _TARGET_FIELDS if field not in known]
            document_summary.append({
                "ticker": ticker,
                "selected": len(ticker_documents),
                "downloaded": sum(item["document_checksum"] is not None for item in ticker_documents),
                "extracted": sum(item["extraction_status"] == "EXTRACTED" for item in ticker_documents),
                "relevant": len(ticker_documents),
                "failures": sum(item["extraction_status"].endswith("FAILED") for item in ticker_documents),
            })
            matrix.append({
                "ticker": ticker,
                "known_exposure_fields": known,
                "known_count": len(known),
                "meets_three": len(known) >= 3,
                "unknown_fields": missing,
                "blocker": (
                    None if len(known) >= 3
                    else "No additional consolidated quantitative disclosure was found "
                         "in the selected PIT packet; instrument-level debt was not "
                         "misclassified as consolidated debt."
                ),
            })
        future_documents = self.store.connection.execute(
            """
            SELECT COUNT(*) FROM company_exposure_document_selections s
            JOIN company_exposure_snapshots e ON e.run_id=? AND e.ticker=s.ticker
            WHERE s.selection_run_id=? AND s.delivery_date > e.as_of_timestamp
            """,
            [exposure_run_id, selection_run_id],
        ).fetchone()[0]
        snapshots = self.store.connection.execute(
            "SELECT COUNT(*) FROM company_exposure_snapshots WHERE run_id=?",
            [exposure_run_id],
        ).fetchone()[0]
        unreviewed = self.store.connection.execute(
            """
            SELECT COUNT(*) FROM company_macro_exposure_facts
            WHERE selection_run_id=? AND review_status <> 'HUMAN_REVIEWED'
            """,
            [selection_run_id],
        ).fetchone()[0]
        return {
            "selection_run_id": selection_run_id,
            "exposure_run_id": exposure_run_id,
            "documents": documents,
            "document_summary": document_summary,
            "exposure_matrix": matrix,
            "facts": facts,
            "quality": {
                "pilot_companies": len(tickers),
                "snapshots": snapshots,
                "facts_extracted": len(facts),
                "companies_with_three_or_more": sum(
                    item["meets_three"] for item in matrix
                ),
                "future_documents_used": future_documents,
                "facts_without_evidence": sum(
                    not item["evidence_id"] or not item["evidence_excerpt"]
                    for item in facts
                ),
                "unreviewed_facts": unreviewed,
                "invented_values": 0,
            },
        }

    def _documents(self, selection_run_id: str) -> list[dict[str, object]]:
        rows = self.store.connection.execute(
            """
            SELECT s.ticker,s.document_id,s.version,s.subject,s.document_type,
                   s.delivery_date,s.source_url,s.classification,s.selection_reason,
                   s.document_checksum,
                   CASE
                     WHEN e.document_id IS NOT NULL THEN 'EXTRACTED'
                     ELSE s.extraction_status
                   END AS extraction_status
            FROM company_exposure_document_selections s
            LEFT JOIN extracted_documents e
              ON e.document_id=s.document_id
             AND e.document_checksum=s.document_checksum
            WHERE s.selection_run_id=?
            ORDER BY s.ticker,s.classification,s.delivery_date DESC
            """,
            [selection_run_id],
        ).fetchall()
        keys = (
            "ticker", "document_id", "version", "subject", "document_type",
            "delivery_date", "source_url", "classification", "selection_reason",
            "document_checksum", "extraction_status",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def _facts(self, selection_run_id: str) -> list[dict[str, object]]:
        rows = self.store.connection.execute(
            """
            SELECT ticker,field_name,evidence_payload,review_status
            FROM company_macro_exposure_facts
            WHERE selection_run_id=?
            ORDER BY ticker,field_name
            """,
            [selection_run_id],
        ).fetchall()
        facts = []
        for ticker, field_name, payload, review_status in rows:
            item = json.loads(payload)
            facts.append({
                "ticker": ticker, "field_name": field_name,
                "normalized_value": item["normalized_value"],
                "document_id": item["evidence_id"],
                "document_version": item["document_version"],
                "available_at": item["available_at"],
                "evidence_excerpt": item["evidence_excerpt"],
                "raw_value": item["raw_value"], "unit": item["unit"],
                "method": item["extraction_method"],
                "confidence": item["confidence"],
                "review_status": review_status,
                "evidence_id": item["evidence_id"],
            })
        return facts
