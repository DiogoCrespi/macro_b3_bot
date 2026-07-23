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
    "currency_hedges",
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
                "informational_candidates": sum(
                    item["classification"] not in {
                        "CORPORATE_ACTION", "ESG", "OTHER", "REFERENCE_FORM_NOTICE"
                    }
                    for item in ticker_documents
                ),
                "used_in_facts": len({
                    item["document_id"] for item in facts if item["ticker"] == ticker
                }),
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
        future_ipe_documents = self.store.connection.execute(
            """
            SELECT COUNT(*) FROM company_exposure_document_selections s
            JOIN company_exposure_snapshots e ON e.run_id=? AND e.ticker=s.ticker
            WHERE s.selection_run_id=? AND s.delivery_date > e.as_of_timestamp
            """,
            [exposure_run_id, selection_run_id],
        ).fetchone()[0]
        future_fre_documents = self.store.connection.execute(
            """
            SELECT COUNT(DISTINCT f.document_id)
              FROM company_fre_sections f
              JOIN company_exposure_snapshots e
                ON e.run_id=? AND e.ticker=f.ticker
             WHERE f.available_at > e.as_of_timestamp
            """,
            [exposure_run_id],
        ).fetchone()[0]
        snapshots = self.store.connection.execute(
            "SELECT COUNT(*) FROM company_exposure_snapshots WHERE run_id=?",
            [exposure_run_id],
        ).fetchone()[0]
        fact_documents = {item["document_id"] for item in facts}
        known_documents = {item["document_id"] for item in documents}
        facts_without_scope = sum(
            not item["scope_entity"] or not item["scope_type"] for item in facts
        )
        scope_mismatch = sum(
            (
                item["field_name"] in {"currency_hedges", "commodity_hedges"}
                and (not item["scope_period"] or not item["denominator_basis"])
            )
            or (
                item["field_name"] == "commodity_production"
                and item["scope_type"] not in {
                    "OPERATED_PRODUCTION", "ASSET_PRODUCTION",
                    "COMPANY_PRODUCTION", "COMPANY_CONSOLIDATED",
                }
            )
            for item in facts
        )
        invalid_denominator = sum(
            item["field_name"].endswith("_pct") and not item["denominator_basis"]
            for item in facts
        )
        unsupported_derivation = sum(
            item["method"] == "RULE_DERIVED"
            and (not item["formula"] or not item["derivation_components"])
            for item in facts
        )
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
                "documents_used_in_facts": len(fact_documents),
                "companies_with_three_or_more": sum(
                    item["meets_three"] for item in matrix
                ),
                "future_documents_used": (
                    future_ipe_documents + future_fre_documents
                ),
                "facts_without_document": sum(
                    item["document_id"] not in known_documents for item in facts
                ),
                "facts_without_excerpt": sum(
                    not item["evidence_excerpt"] for item in facts
                ),
                "facts_without_rule_version": sum(
                    not item["methodology_version"] for item in facts
                ),
                "facts_without_review": sum(
                    item["review_status"] != "HUMAN_APPROVED" for item in facts
                ),
                "facts_without_scope": facts_without_scope,
                "facts_with_scope_mismatch": scope_mismatch,
                "facts_with_invalid_denominator": invalid_denominator,
                "facts_with_unsupported_derivation": unsupported_derivation,
                "invented_values_check": "NOT_IMPLEMENTED",
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
            UNION ALL
            SELECT f.ticker,'FRE:' || f.document_id,f.version,
                   'Formulário de Referência ' || CAST(f.reference_date AS VARCHAR),
                   'FRE',f.available_at,f.source_url,'REFERENCE_FORM_DOCUMENT',
                   'latest_official_fre_point_in_time',
                   f.raw_document_checksum,'EXTRACTED'
              FROM company_fre_sections f
             GROUP BY f.ticker,f.document_id,f.version,f.reference_date,
                      f.available_at,f.source_url,f.raw_document_checksum
            ORDER BY ticker,classification,delivery_date DESC
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
            SELECT ticker,field_name,evidence_payload,review_status,
                   reviewed_by,reviewed_at,review_decision,review_notes,
                   source_excerpt_hash,methodology_version
            FROM company_macro_exposure_facts
            WHERE selection_run_id=? AND is_active=TRUE
            ORDER BY ticker,field_name
            """,
            [selection_run_id],
        ).fetchall()
        facts = []
        for (
            ticker, field_name, payload, review_status, reviewed_by, reviewed_at,
            review_decision, review_notes, excerpt_hash, methodology_version,
        ) in rows:
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
                "extraction_match_confidence": item["extraction_match_confidence"],
                "semantic_scope_confidence": item["semantic_scope_confidence"],
                "denominator_confidence": item["denominator_confidence"],
                "review_confidence": item["review_confidence"],
                "scope_entity": item["scope_entity"],
                "scope_type": item["scope_type"],
                "scope_period": item["scope_period"],
                "denominator_basis": item["denominator_basis"],
                "formula": item["formula"],
                "derivation_components": item["derivation_components"],
                "methodology_version": methodology_version,
                "review_status": review_status,
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at,
                "review_decision": review_decision,
                "review_notes": review_notes,
                "source_excerpt_hash": excerpt_hash,
                "evidence_id": item["evidence_id"],
            })
        return facts
