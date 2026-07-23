"""Point-in-time CVM exposure builder for the Sprint 4C.1 pilot."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureSnapshot,
    ExposureFieldEvidence,
    ExtractionMethod,
)
from macro_b3_bot.infrastructure.store import DatabaseStore

_PILOT_PATH = Path(__file__).resolve().parents[3] / "config" / "company_exposure_pilot.yaml"
_EXPOSURE_FIELDS = (
    "total_revenue", "foreign_revenue", "total_debt", "gross_financial_debt",
    "foreign_currency_debt",
    "floating_rate_debt", "inflation_linked_debt", "revenue_foreign_currency_pct",
    "cost_foreign_currency_pct", "export_revenue_pct", "floating_rate_debt_pct",
    "inflation_linked_debt_pct", "foreign_currency_debt_pct", "commodity_exposures",
    "geographic_exposures", "demand_cyclicality", "pricing_power", "operating_leverage",
)


class CompanyExposureBuilder:
    """Build only fields supported by documents available at the requested cutoff."""

    def __init__(
        self,
        store: DatabaseStore,
        run_id: str,
        methodology_version: str = "4C.3-v1",
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.methodology_version = methodology_version

    @staticmethod
    def load_pilot(path: Path = _PILOT_PATH) -> list[dict[str, str]]:
        with open(path, encoding="utf-8") as stream:
            return list((yaml.safe_load(stream) or {}).get("companies", []))

    def build_pilot(
        self, as_of_timestamp: datetime, companies: Optional[list[dict[str, str]]] = None
    ) -> dict[str, object]:
        companies = companies or self.load_pilot()
        snapshots: list[CompanyExposureSnapshot] = []
        missing_mapping: list[str] = []
        missing_document: list[str] = []
        for company in companies:
            result, reason = self.build_snapshot(
                company["ticker"], company["sector"], as_of_timestamp
            )
            if result is None:
                (missing_mapping if reason == "MISSING_MAPPING" else missing_document).append(
                    company["ticker"]
                )
                continue
            self.store.save_company_exposure_snapshot(result.model_dump(mode="json"))
            snapshots.append(result)
        return {
            "run_id": self.run_id,
            "as_of_timestamp": self._utc(as_of_timestamp).isoformat(),
            "pilot_requested": len(companies),
            "snapshots_built": len(snapshots),
            "missing_mapping": missing_mapping,
            "missing_point_in_time_document": missing_document,
            "snapshots": snapshots,
        }

    def build_snapshot(
        self, ticker: str, sector: str, as_of_timestamp: datetime
    ) -> tuple[CompanyExposureSnapshot | None, str | None]:
        as_of = self._utc(as_of_timestamp)
        cutoff = self._db_timestamp(as_of)
        mapping = self.store.connection.execute(
            """
            SELECT ticker,cvm_code FROM company_ticker_map
            WHERE ticker = ? AND validated = TRUE
              AND review_status = 'VALIDATED'
              AND valid_from <= CAST(? AS DATE)
              AND (valid_to IS NULL OR valid_to >= CAST(? AS DATE))
              AND created_at <= ?
            ORDER BY confidence DESC,created_at DESC LIMIT 1
            """,
            [ticker, as_of.date(), as_of.date(), cutoff],
        ).fetchone()
        if not mapping or not mapping[1]:
            return None, "MISSING_MAPPING"
        cvm_code = str(mapping[1])
        documents = self.store.connection.execute(
            """
            WITH available_documents AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY cvm_code,document_type,reference_date
                           ORDER BY version DESC,received_at DESC,document_id DESC
                       ) AS rn
                FROM cvm_documents
                WHERE cvm_code = ?
                  AND COALESCE(filing_available_at,resource_last_modified_at,
                               received_at,collected_at) <= ?
                  AND document_type IN ('DFP','ITR')
            )
            SELECT document_id,document_type,reference_date,
                   COALESCE(filing_available_at,resource_last_modified_at,
                            received_at,collected_at) AS available_at,
                   version,availability_precision
            FROM available_documents
            WHERE rn = 1
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY document_type
                ORDER BY reference_date DESC,received_at DESC,version DESC
            ) = 1
            ORDER BY CASE WHEN document_type='ITR' THEN 0 ELSE 1 END
            """,
            [cvm_code, cutoff],
        ).fetchall()
        if not documents:
            return None, "MISSING_DOCUMENT"

        primary = documents[0]
        document_id, document_type, reference_date, received_at, version, _ = primary
        values: dict[str, object] = {field: None for field in _EXPOSURE_FIELDS}
        evidence: list[ExposureFieldEvidence] = []
        revenue, revenue_doc = self._best_statement_value(documents, "3.01")
        # Those standardized accounts represent interbank relations/deposits
        # for financial institutions, not comparable corporate debt.
        if sector == "BANCOS":
            debt_current = debt_long = None
            current_doc = long_doc = None
            total_debt = None
        else:
            debt_current, current_doc = self._best_statement_value(documents, "2.01.04")
            debt_long, long_doc = self._best_statement_value(documents, "2.02.01")
            total_debt = (
                sum(item for item in (debt_current, debt_long) if item is not None)
                if debt_current is not None or debt_long is not None else None
            )
        if revenue is not None:
            values["total_revenue"] = revenue
            assert revenue_doc is not None
            evidence.append(self._evidence(
                "total_revenue", revenue, f"CVM_{revenue_doc[1]}",
                str(revenue_doc[0]), revenue_doc[3],
                "Standardized DRE account 3.01 from the selected point-in-time filing.",
            ))
        if total_debt is not None:
            values["total_debt"] = total_debt
            values["gross_financial_debt"] = total_debt
            debt_docs = [item for item in (current_doc, long_doc) if item is not None]
            debt_evidence_id = "+".join(sorted({str(item[0]) for item in debt_docs}))
            debt_available_at = max(item[3] for item in debt_docs)
            debt_source_types = "+".join(sorted({f"CVM_{item[1]}" for item in debt_docs}))
            evidence.append(self._evidence(
                "total_debt", total_debt, debt_source_types, debt_evidence_id,
                debt_available_at,
                "Compatibility alias for gross_financial_debt.",
            ))
            evidence.append(self._evidence(
                "gross_financial_debt", total_debt, debt_source_types, debt_evidence_id,
                debt_available_at,
                "Gross financial debt v1: standardized accounts 2.01.04 and "
                "2.02.01; excludes cash/netting and is not applied to banks.",
            ))

        for override in self.store.get_company_exposure_overrides_as_of(ticker, as_of):
            field_name = override["field_name"]
            if field_name not in values:
                continue
            values[field_name] = override["new_value"]
            evidence.append(ExposureFieldEvidence(
                field_name=field_name, value=override["new_value"],
                source_type="AUDITED_OVERRIDE", evidence_id=override["evidence_ids"][0],
                available_at=self._utc(override["approved_at"]),
                extraction_method=ExtractionMethod.AUDITED_OVERRIDE,
                methodology_version=override["methodology_version"], confidence=1.0,
                is_estimated=False, rationale=override["rationale"],
            ))

        missing = [field for field in _EXPOSURE_FIELDS if values[field] is None]
        evidence_quality = (
            sum(item.confidence for item in evidence) / len(evidence) if evidence else 0.0
        )
        completeness = sum(values[field] is not None for field in _EXPOSURE_FIELDS) / len(
            _EXPOSURE_FIELDS
        )
        confidence = (evidence_quality * completeness) ** 0.5
        identity = (
            f"{ticker}|{cvm_code}|{as_of.isoformat()}|{document_id}|{version}|"
            f"{','.join(str(item[0]) for item in documents)}|"
            f"{self.methodology_version}|{self.run_id}"
        )
        snapshot = CompanyExposureSnapshot(
            exposure_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=ticker, cvm_code=cvm_code, sector=sector,
            as_of_timestamp=as_of, reference_date=reference_date,
            exposure_version=self.methodology_version, **values,
            field_evidence=evidence, missing_fields=missing,
            evidence_quality_score=round(evidence_quality, 4),
            completeness_score=round(completeness, 4),
            confidence=round(confidence, 4), run_id=self.run_id,
            created_at=datetime.now(timezone.utc),
        )
        return snapshot, None

    def _statement_value(self, document_id: str, account_code: str) -> float | None:
        row = self.store.connection.execute(
            """
            SELECT CAST(value AS DOUBLE) * CASE WHEN scale > 0 THEN scale ELSE 1 END
            FROM financial_statement_lines
            WHERE document_id = ? AND account_code = ?
            ORDER BY CASE WHEN scope IN ('CONSOLIDATED','CONSOLIDADO') THEN 0 ELSE 1 END,
                     end_date DESC,
                     CASE WHEN fiscal_order IN ('ÚLTIMO','LAST') THEN 0 ELSE 1 END
            LIMIT 1
            """,
            [document_id, account_code],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def _best_statement_value(
        self, documents: list[tuple], account_code: str
    ) -> tuple[float | None, tuple | None]:
        for document in documents:
            value = self._statement_value(str(document[0]), account_code)
            if value is not None:
                return value, document
        return None, None

    def _evidence(
        self, field_name: str, value: float, source_type: str, evidence_id: str,
        available_at: datetime, rationale: str,
    ) -> ExposureFieldEvidence:
        return ExposureFieldEvidence(
            field_name=field_name, value=value, source_type=source_type,
            evidence_id=evidence_id, available_at=self._utc(available_at),
            extraction_method=ExtractionMethod.STATEMENT_DERIVED,
            methodology_version=self.methodology_version, confidence=0.98,
            is_estimated=False, rationale=rationale,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _db_timestamp(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
