"""Reproducible accounting trace for the 15-company exposure pilot."""
from __future__ import annotations

import json
from typing import Any

from macro_b3_bot.infrastructure.store import DatabaseStore


class CompanyExposureAuditor:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store

    def audit_run(self, run_id: str) -> list[dict[str, Any]]:
        snapshots = self.store.connection.execute(
            """
            SELECT ticker,sector,exposure_payload,field_evidence
            FROM company_exposure_snapshots
            WHERE run_id = ?
            ORDER BY ticker
            """,
            [run_id],
        ).fetchall()
        rows: list[dict[str, Any]] = []
        for ticker, sector, payload_json, evidence_json in snapshots:
            payload = json.loads(payload_json)
            evidence = json.loads(evidence_json)
            evidence_by_field = {item["field_name"]: item for item in evidence}
            rows.append(self._audit_metric(
                ticker, sector, "total_revenue", "3.01",
                payload.get("total_revenue"), evidence_by_field.get("total_revenue"),
            ))
            if sector != "BANCOS":
                rows.append(self._audit_debt(
                    ticker, sector, payload.get("gross_financial_debt"),
                    evidence_by_field.get("gross_financial_debt"),
                ))
        return rows

    def _audit_metric(
        self, ticker: str, sector: str, metric: str, account: str,
        extracted: float | None, evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        document_ids = self._document_ids(evidence)
        component = self._first_component(document_ids, account)
        published = component["normalized_value"] if component else None
        return self._row(
            ticker, sector, metric, account, extracted, published,
            [component] if component else [], evidence,
        )

    def _audit_debt(
        self, ticker: str, sector: str, extracted: float | None,
        evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        document_ids = self._document_ids(evidence)
        components = [
            component for account in ("2.01.04", "2.02.01")
            if (component := self._first_component(document_ids, account)) is not None
        ]
        published = sum(item["normalized_value"] for item in components) if components else None
        return self._row(
            ticker, sector, "gross_financial_debt", "2.01.04 + 2.02.01",
            extracted, published, components, evidence,
        )

    def _first_component(
        self, document_ids: list[str], account: str
    ) -> dict[str, Any] | None:
        for document_id in sorted(
            document_ids, key=lambda value: (not value.startswith("ITR_"), value)
        ):
            row = self.store.connection.execute(
                """
                SELECT d.document_type,d.reference_date,
                       COALESCE(d.filing_available_at,d.resource_last_modified_at,
                                d.received_at,d.collected_at),
                       d.version,d.availability_precision,l.value,l.scale,
                       CAST(l.value AS DOUBLE) *
                           CASE WHEN l.scale > 0 THEN l.scale ELSE 1 END
                FROM financial_statement_lines l
                JOIN cvm_documents d USING (document_id)
                WHERE l.document_id = ? AND l.account_code = ?
                ORDER BY CASE WHEN l.scope IN ('CONSOLIDATED','CONSOLIDADO')
                              THEN 0 ELSE 1 END,
                         l.end_date DESC,
                         CASE WHEN l.fiscal_order IN ('ÚLTIMO','LAST') THEN 0 ELSE 1 END
                LIMIT 1
                """,
                [document_id, account],
            ).fetchone()
            if row:
                return {
                    "document_id": document_id, "document_type": row[0],
                    "reference_date": str(row[1]), "available_at": str(row[2]),
                    "version": row[3], "availability_precision": row[4],
                    "account": account, "raw_value": float(row[5]),
                    "scale": row[6], "normalized_value": float(row[7]),
                }
        return None

    @staticmethod
    def _document_ids(evidence: dict[str, Any] | None) -> list[str]:
        return evidence["evidence_id"].split("+") if evidence else []

    @staticmethod
    def _row(
        ticker: str, sector: str, metric: str, formula: str,
        extracted: float | None, published: float | None,
        components: list[dict[str, Any]], evidence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        difference = (
            extracted - published
            if extracted is not None and published is not None else None
        )
        pct = (
            difference / published
            if difference is not None and published not in (None, 0) else None
        )
        return {
            "ticker": ticker, "sector": sector, "metric": metric, "formula": formula,
            "extracted_value": extracted, "published_normalized_value": published,
            "absolute_difference": abs(difference) if difference is not None else None,
            "percentage_difference": abs(pct) if pct is not None else None,
            "components": components,
            "evidence_id": evidence.get("evidence_id") if evidence else None,
            "validation_status": (
                "VALIDATED" if difference == 0 else "MISSING" if difference is None
                else "MISMATCH"
            ),
        }
