"""Build point-in-time TTM financial baselines from official CVM statements."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from typing import Any

from macro_b3_bot.domain.company_exposure_models import CompanyExposureSnapshot
from macro_b3_bot.domain.financial_bridge_models import (
    FinancialBaselineSnapshot,
    FinancialFieldEvidence,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


_FLOW_ACCOUNTS = {
    "ttm_revenue": ("DRE", "3.01"),
    "ttm_costs": ("DRE", "3.02"),
    "ttm_ebit": ("DRE", "3.05"),
    "ttm_financial_result": ("DRE", "3.06"),
    "ttm_pre_tax_income": ("DRE", "3.07"),
    "ttm_net_income": ("DRE", "3.11"),
    "ttm_operating_cash_flow": ("DFC-MI", "6.01"),
}
_BALANCE_ACCOUNTS = {
    "cash": ("BPA", ("1.01.01",)),
    "gross_debt": ("BPP", ("2.01.04", "2.02.01")),
    "current_assets": ("BPA", ("1.01",)),
    "current_liabilities": ("BPP", ("2.01",)),
}


class FinancialBaselineBuilder:
    """Normalize FY + current quarter - comparative quarter into a PIT TTM."""

    def __init__(
        self,
        store: DatabaseStore,
        run_id: str,
        methodology_version: str = "4D.1-baseline-v1",
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.methodology_version = methodology_version

    def build(
        self,
        ticker: str,
        as_of_timestamp: datetime,
        exposure: CompanyExposureSnapshot,
    ) -> FinancialBaselineSnapshot:
        as_of = self._utc(as_of_timestamp)
        documents = self._documents(ticker, as_of)
        if "ITR" not in documents or "DFP" not in documents:
            raise ValueError(f"{ticker}: PIT ITR and DFP are required")
        latest_quarter = documents["ITR"]["reference_date"]
        values: dict[str, float | None] = {}
        evidence: list[FinancialFieldEvidence] = []

        for field, (statement, account) in _FLOW_ACCOUNTS.items():
            value, item = self._ttm_account(documents, statement, account)
            values[field] = value
            evidence.append(self._ttm_evidence(field, item, latest_quarter))

        capex_value, capex_item = self._ttm_capex(documents)
        values["ttm_capex"] = capex_value
        evidence.append(self._ttm_evidence(
            "ttm_capex", capex_item, latest_quarter
        ))

        da_value, da_item = self._ttm_da(documents)
        values["ttm_ebitda"] = (
            values["ttm_ebit"] + da_value
            if values["ttm_ebit"] is not None and da_value is not None
            else None
        )
        if values["ttm_ebitda"] is not None:
            evidence.append(self._derived_evidence(
                "ttm_ebitda", values["ttm_ebitda"], latest_quarter,
                "ttm_ebit + ttm_depreciation_and_amortization",
                {
                    "ttm_ebit": values["ttm_ebit"],
                    "ttm_depreciation_and_amortization": da_value,
                },
                da_item["source_ids"] + self._field_sources(evidence, "ttm_ebit"),
                da_item["source_locations"] + self._field_locations(evidence, "ttm_ebit"),
                da_item["available_at"] + self._field_available(evidence, "ttm_ebit"),
            ))

        balances: dict[str, float] = {}
        balance_meta: dict[str, dict[str, Any]] = {}
        for field, (statement, accounts) in _BALANCE_ACCOUNTS.items():
            current, current_meta = self._balance(
                documents["ITR"], statement, accounts, "ÚLTIMO"
            )
            balances[field] = current
            balance_meta[field] = current_meta
            if field in {"cash", "gross_debt"}:
                evidence.append(self._reported_evidence(
                    field, current, latest_quarter, current_meta
                ))

        working_capital = balances["current_assets"] - balances["current_liabilities"]
        evidence.append(self._derived_evidence(
            "working_capital", working_capital, latest_quarter,
            "current_assets - current_liabilities",
            {
                "current_assets": balances["current_assets"],
                "current_liabilities": balances["current_liabilities"],
            },
            balance_meta["current_assets"]["source_ids"]
            + balance_meta["current_liabilities"]["source_ids"],
            balance_meta["current_assets"]["source_locations"]
            + balance_meta["current_liabilities"]["source_locations"],
            balance_meta["current_assets"]["available_at"]
            + balance_meta["current_liabilities"]["available_at"],
        ))

        prior_debt, prior_debt_meta = self._balance(
            documents["DFP"], "BPP", ("2.01.04", "2.02.01"), "ÚLTIMO"
        )
        average_gross_debt = (prior_debt + balances["gross_debt"]) / 2
        evidence.append(self._derived_evidence(
            "average_gross_debt", average_gross_debt, latest_quarter,
            "(gross_debt_fy_end + gross_debt_latest_quarter) / 2",
            {
                "gross_debt_fy_end": prior_debt,
                "gross_debt_latest_quarter": balances["gross_debt"],
            },
            prior_debt_meta["source_ids"] + balance_meta["gross_debt"]["source_ids"],
            prior_debt_meta["source_locations"]
            + balance_meta["gross_debt"]["source_locations"],
            prior_debt_meta["available_at"] + balance_meta["gross_debt"]["available_at"],
        ))

        fcf = values["ttm_operating_cash_flow"] + values["ttm_capex"]
        evidence.append(self._derived_from_fields(
            "ttm_fcf", fcf, latest_quarter,
            "ttm_operating_cash_flow + ttm_capex",
            {
                "ttm_operating_cash_flow": values["ttm_operating_cash_flow"],
                "ttm_capex": values["ttm_capex"],
            },
            evidence,
        ))
        net_debt = balances["gross_debt"] - balances["cash"]
        evidence.append(self._derived_from_fields(
            "net_debt", net_debt, latest_quarter,
            "gross_debt - cash",
            {"gross_debt": balances["gross_debt"], "cash": balances["cash"]},
            evidence,
        ))

        tax_rate, tax_item = self._tax_rate(documents)
        if tax_rate is not None:
            evidence.append(self._derived_evidence(
                "effective_tax_rate", tax_rate, latest_quarter,
                "min(max(-ttm_tax_expense / ttm_pre_tax_income, 0), 1)",
                {
                    "ttm_tax_expense": tax_item["tax"],
                    "ttm_pre_tax_income": values["ttm_pre_tax_income"],
                },
                tax_item["source_ids"]
                + self._field_sources(evidence, "ttm_pre_tax_income"),
                tax_item["source_locations"]
                + self._field_locations(evidence, "ttm_pre_tax_income"),
                tax_item["available_at"]
                + self._field_available(evidence, "ttm_pre_tax_income"),
                unit="RATIO",
            ))

        derived_debt = self._debt_exposure_values(
            average_gross_debt, exposure, latest_quarter, evidence
        )
        for field, (value, field_evidence) in derived_debt.items():
            values[field] = value
            evidence.append(field_evidence)

        identity = (
            f"{self.run_id}|{ticker}|{as_of.isoformat()}|{latest_quarter}|"
            f"{self.methodology_version}"
        )
        missing = [
            name for name in (
                "ttm_ebitda", "average_floating_debt",
                "average_net_fx_debt", "inflation_linked_debt",
                "effective_tax_rate",
            )
            if values.get(name) is None and (
                name != "effective_tax_rate" or tax_rate is None
            )
        ]
        confidence = sum(item.confidence for item in evidence) / len(evidence)
        snapshot = FinancialBaselineSnapshot(
            baseline_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            ticker=ticker,
            cvm_code=exposure.cvm_code,
            as_of_timestamp=as_of,
            latest_quarter=latest_quarter,
            methodology_version=self.methodology_version,
            ttm_revenue=values["ttm_revenue"],
            ttm_costs=values["ttm_costs"],
            ttm_ebit=values["ttm_ebit"],
            ttm_ebitda=values["ttm_ebitda"],
            ttm_financial_result=values["ttm_financial_result"],
            ttm_pre_tax_income=values["ttm_pre_tax_income"],
            ttm_net_income=values["ttm_net_income"],
            ttm_operating_cash_flow=values["ttm_operating_cash_flow"],
            ttm_capex=values["ttm_capex"],
            ttm_fcf=fcf,
            gross_debt=balances["gross_debt"],
            cash=balances["cash"],
            net_debt=net_debt,
            average_gross_debt=average_gross_debt,
            average_floating_debt=values.get("average_floating_debt"),
            average_net_fx_debt=values.get("average_net_fx_debt"),
            inflation_linked_debt=values.get("inflation_linked_debt"),
            effective_tax_rate=tax_rate,
            working_capital=working_capital,
            field_evidence=evidence,
            missing_fields=missing,
            confidence=round(confidence, 4),
            run_id=self.run_id,
            created_at=datetime.now(timezone.utc),
        )
        self.store.save_financial_baseline(snapshot.model_dump(mode="json"))
        return snapshot

    def _documents(self, ticker: str, as_of: datetime) -> dict[str, dict[str, Any]]:
        rows = self.store.connection.execute(
            """
            SELECT d.document_id,d.document_type,d.reference_date,d.version,
                   COALESCE(d.filing_available_at,d.resource_last_modified_at,
                            d.received_at,d.collected_at) AS available_at
              FROM company_ticker_map m
              JOIN cvm_documents d ON d.cvm_code=m.cvm_code
             WHERE m.ticker=? AND m.validated=TRUE
               AND d.document_type IN ('ITR','DFP')
               AND COALESCE(d.filing_available_at,d.resource_last_modified_at,
                            d.received_at,d.collected_at) <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY d.document_type
                ORDER BY d.reference_date DESC,d.version DESC,available_at DESC
            )=1
            """,
            [ticker, as_of.replace(tzinfo=None)],
        ).fetchall()
        return {
            row[1]: {
                "document_id": row[0], "document_type": row[1],
                "reference_date": row[2], "version": row[3],
                "available_at": row[4].replace(tzinfo=timezone.utc),
            }
            for row in rows
        }

    def _ttm_account(
        self, documents: dict[str, dict[str, Any]], statement: str, account: str
    ) -> tuple[float, dict[str, Any]]:
        annual, annual_meta = self._line(
            documents["DFP"], statement, account, "ÚLTIMO"
        )
        current, current_meta = self._line(
            documents["ITR"], statement, account, "ÚLTIMO"
        )
        prior, prior_meta = self._line(
            documents["ITR"], statement, account, "PENÚLTIMO"
        )
        return annual + current - prior, {
            "annual": annual, "current_quarter": current,
            "prior_comparative_quarter": prior,
            "source_ids": annual_meta["source_ids"] + current_meta["source_ids"]
            + prior_meta["source_ids"],
            "source_locations": annual_meta["source_locations"]
            + current_meta["source_locations"] + prior_meta["source_locations"],
            "available_at": annual_meta["available_at"]
            + current_meta["available_at"] + prior_meta["available_at"],
        }

    def _ttm_da(
        self, documents: dict[str, dict[str, Any]]
    ) -> tuple[float | None, dict[str, Any]]:
        parts: dict[str, tuple[float, dict[str, Any]]] = {}
        for label, document, order in (
            ("annual", documents["DFP"], "ÚLTIMO"),
            ("current_quarter", documents["ITR"], "ÚLTIMO"),
            ("prior_comparative_quarter", documents["ITR"], "PENÚLTIMO"),
        ):
            rows = self.store.connection.execute(
                """
                SELECT value,scale,account_code,account_description
                  FROM financial_statement_lines
                 WHERE document_id=? AND statement_type='DFC-MI'
                   AND scope='CONSOLIDATED' AND fiscal_order=?
                   AND account_code LIKE '6.01.01%'
                   AND (
                       account_code IN ('6.01.01.02','6.01.01.03')
                       OR
                       LOWER(account_description) LIKE '%deprecia%'
                       OR LOWER(account_description) LIKE '%exaust%'
                       OR LOWER(account_description) LIKE '%amortiza%'
                   )
                   AND LOWER(account_description) NOT LIKE '%custo de transa%'
                """,
                [document["document_id"], order],
            ).fetchall()
            if not rows:
                return None, {"source_ids": [], "source_locations": [], "available_at": []}
            # Keep the broadest disclosed D&A rows and avoid child double counting.
            min_depth = min(str(row[2]).count(".") for row in rows)
            selected = [row for row in rows if str(row[2]).count(".") == min_depth]
            value = sum(float(row[0]) * int(row[1]) for row in selected)
            parts[label] = (value, {
                "source_ids": [document["document_id"]],
                "source_locations": [
                    f"{document['document_id']}:DFC-MI:{row[2]}:{order}"
                    for row in selected
                ],
                "available_at": [document["available_at"]],
            })
        value = parts["annual"][0] + parts["current_quarter"][0] - parts[
            "prior_comparative_quarter"
        ][0]
        return value, {
            "source_ids": sum((item[1]["source_ids"] for item in parts.values()), []),
            "source_locations": sum((
                item[1]["source_locations"] for item in parts.values()
            ), []),
            "available_at": sum((
                item[1]["available_at"] for item in parts.values()
            ), []),
        }

    def _ttm_capex(
        self, documents: dict[str, dict[str, Any]]
    ) -> tuple[float, dict[str, Any]]:
        parts: dict[str, tuple[float, dict[str, Any]]] = {}
        for label, document, order in (
            ("annual", documents["DFP"], "ÚLTIMO"),
            ("current_quarter", documents["ITR"], "ÚLTIMO"),
            ("prior_comparative_quarter", documents["ITR"], "PENÚLTIMO"),
        ):
            rows = self.store.connection.execute(
                """
                SELECT value,scale,account_code,account_description
                  FROM financial_statement_lines
                 WHERE document_id=? AND statement_type='DFC-MI'
                   AND scope='CONSOLIDATED' AND fiscal_order=?
                   AND account_code LIKE '6.02%'
                   AND value <= 0
                   AND (
                       LOWER(account_description) LIKE '%aquisi%imobil%'
                       OR LOWER(account_description) LIKE '%aquisicao%imobil%'
                       OR LOWER(account_description) LIKE '%aquisi%intang%'
                       OR LOWER(account_description) LIKE '%adi%imobil%'
                       OR LOWER(account_description) LIKE '%adi%intang%'
                       OR LOWER(account_description) LIKE '%adi%ativo biol%'
                       OR LOWER(account_description) LIKE 'em imobil%'
                       OR LOWER(account_description) LIKE 'em intang%'
                   )
                ORDER BY account_code
                """,
                [document["document_id"], order],
            ).fetchall()
            if not rows:
                value, meta = self._line(
                    document, "DFC-MI", "6.02.01", order
                )
            else:
                value = sum(float(row[0]) * int(row[1]) for row in rows)
                meta = {
                    "source_ids": [document["document_id"]],
                    "source_locations": [
                        f"{document['document_id']}:DFC-MI:{row[2]}:{order}"
                        for row in rows
                    ],
                    "available_at": [document["available_at"]],
                }
            parts[label] = (value, meta)
        value = parts["annual"][0] + parts["current_quarter"][0] - parts[
            "prior_comparative_quarter"
        ][0]
        return value, {
            "annual": parts["annual"][0],
            "current_quarter": parts["current_quarter"][0],
            "prior_comparative_quarter": parts["prior_comparative_quarter"][0],
            "source_ids": sum((item[1]["source_ids"] for item in parts.values()), []),
            "source_locations": sum((
                item[1]["source_locations"] for item in parts.values()
            ), []),
            "available_at": sum((
                item[1]["available_at"] for item in parts.values()
            ), []),
        }

    def _line(
        self, document: dict[str, Any], statement: str, account: str, order: str
    ) -> tuple[float, dict[str, Any]]:
        row = self.store.connection.execute(
            """
            SELECT value,scale,currency
              FROM financial_statement_lines
             WHERE document_id=? AND statement_type=? AND scope='CONSOLIDATED'
               AND fiscal_order=? AND account_code=?
             ORDER BY record_checksum LIMIT 1
            """,
            [document["document_id"], statement, order, account],
        ).fetchone()
        if not row:
            raise ValueError(
                f"missing line {document['document_id']}:{statement}:{account}:{order}"
            )
        return float(row[0]) * int(row[1]), {
            "source_ids": [document["document_id"]],
            "source_locations": [
                f"{document['document_id']}:{statement}:{account}:{order}"
            ],
            "available_at": [document["available_at"]],
            "currency": row[2],
        }

    def _balance(
        self,
        document: dict[str, Any],
        statement: str,
        accounts: tuple[str, ...],
        order: str,
    ) -> tuple[float, dict[str, Any]]:
        values = [self._line(document, statement, account, order) for account in accounts]
        return sum(item[0] for item in values), {
            "source_ids": sum((item[1]["source_ids"] for item in values), []),
            "source_locations": sum((
                item[1]["source_locations"] for item in values
            ), []),
            "available_at": sum((item[1]["available_at"] for item in values), []),
        }

    def _tax_rate(
        self, documents: dict[str, dict[str, Any]]
    ) -> tuple[float | None, dict[str, Any]]:
        tax, tax_item = self._ttm_account(documents, "DRE", "3.08")
        pretax, _ = self._ttm_account(documents, "DRE", "3.07")
        if pretax <= 0 or tax >= 0:
            return None, {**tax_item, "tax": tax}
        return round(min(max(-tax / pretax, 0), 1), 6), {
            **tax_item, "tax": tax,
        }

    def _debt_exposure_values(
        self,
        average_debt: float,
        exposure: CompanyExposureSnapshot,
        period_end: date,
        baseline_evidence: list[FinancialFieldEvidence],
    ) -> dict[str, tuple[float, FinancialFieldEvidence]]:
        result: dict[str, tuple[float, FinancialFieldEvidence]] = {}
        mappings = {
            "average_floating_debt": (
                "post_hedge_floating_rate_debt_pct",
                "floating_rate_debt_pct",
            ),
            "average_net_fx_debt": ("net_foreign_currency_debt_pct",),
            "inflation_linked_debt": ("inflation_linked_debt_pct",),
        }
        for output, exposure_fields in mappings.items():
            exposure_field = next((
                field for field in exposure_fields
                if getattr(exposure, field) is not None
            ), exposure_fields[0])
            pct = getattr(exposure, exposure_field)
            if pct is None:
                continue
            value = average_debt * pct
            matching = [
                item for item in exposure.field_evidence
                if item.field_name == exposure_field
            ]
            average_sources = self._field_sources(
                baseline_evidence, "average_gross_debt"
            )
            average_locations = self._field_locations(
                baseline_evidence, "average_gross_debt"
            )
            average_available = self._field_available(
                baseline_evidence, "average_gross_debt"
            )
            result[output] = (value, FinancialFieldEvidence(
                field_name=output,
                source_ids=average_sources + [
                    item.evidence_id for item in matching
                ],
                source_locations=average_locations + [
                    f"CompanyExposureSnapshot:{exposure.exposure_id}:{exposure_field}"
                    for _ in matching
                ],
                available_at=average_available + [
                    item.available_at for item in matching
                ],
                period_end=period_end,
                formula=f"average_gross_debt * {exposure_field}",
                components={
                    "average_gross_debt": average_debt,
                    exposure_field: pct,
                },
                evidence_label="derived_calculation",
                confidence=min(
                    [item.confidence for item in matching] + [exposure.confidence]
                ),
                notes="Economic post-hedge exposure; contractual debt is not substituted.",
            ))
        return result

    def _ttm_evidence(
        self, field: str, item: dict[str, Any], period_end: date
    ) -> FinancialFieldEvidence:
        return self._derived_evidence(
            field,
            item["annual"] + item["current_quarter"]
            - item["prior_comparative_quarter"],
            period_end,
            "FY_latest + Q_current - Q_prior_comparative",
            {
                "FY_latest": item["annual"],
                "Q_current": item["current_quarter"],
                "Q_prior_comparative": item["prior_comparative_quarter"],
            },
            item["source_ids"], item["source_locations"], item["available_at"],
        )

    @staticmethod
    def _reported_evidence(
        field: str, value: float, period_end: date, meta: dict[str, Any]
    ) -> FinancialFieldEvidence:
        return FinancialFieldEvidence(
            field_name=field,
            source_ids=meta["source_ids"],
            source_locations=meta["source_locations"],
            available_at=meta["available_at"],
            period_end=period_end,
            formula="sum(reported_standardized_accounts)",
            components={"reported_value": value},
            evidence_label="fact_source_reported",
            confidence=0.98,
        )

    @staticmethod
    def _derived_evidence(
        field: str,
        value: float,
        period_end: date,
        formula: str,
        components: dict[str, float],
        source_ids: list[str],
        locations: list[str],
        available_at: list[datetime],
        unit: str = "BRL",
    ) -> FinancialFieldEvidence:
        return FinancialFieldEvidence(
            field_name=field,
            source_ids=source_ids,
            source_locations=locations,
            available_at=available_at,
            period_end=period_end,
            unit=unit,
            formula=formula,
            components=components,
            evidence_label="derived_calculation",
            confidence=0.96,
            notes=f"Normalized value: {value:.2f} BRL.",
        )

    def _derived_from_fields(
        self,
        field: str,
        value: float,
        period_end: date,
        formula: str,
        components: dict[str, float],
        evidence: list[FinancialFieldEvidence],
    ) -> FinancialFieldEvidence:
        fields = list(components)
        return self._derived_evidence(
            field, value, period_end, formula, components,
            sum((self._field_sources(evidence, item) for item in fields), []),
            sum((self._field_locations(evidence, item) for item in fields), []),
            sum((self._field_available(evidence, item) for item in fields), []),
        )

    @staticmethod
    def _field_sources(
        evidence: list[FinancialFieldEvidence], field: str
    ) -> list[str]:
        return sum((item.source_ids for item in evidence if item.field_name == field), [])

    @staticmethod
    def _field_locations(
        evidence: list[FinancialFieldEvidence], field: str
    ) -> list[str]:
        return sum((
            item.source_locations for item in evidence if item.field_name == field
        ), [])

    @staticmethod
    def _field_available(
        evidence: list[FinancialFieldEvidence], field: str
    ) -> list[datetime]:
        return sum((item.available_at for item in evidence if item.field_name == field), [])

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
