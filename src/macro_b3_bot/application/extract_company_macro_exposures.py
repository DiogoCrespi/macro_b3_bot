"""Deterministic extraction of explicitly disclosed company macro exposures."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable

from macro_b3_bot.domain.company_exposure_models import (
    ExposureFieldEvidence,
    ExtractionMethod,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


@dataclass(frozen=True)
class SourceDocument:
    ticker: str
    document_id: str
    version: int
    available_at: datetime
    classification: str
    text: str


@dataclass(frozen=True)
class ExtractionRule:
    ticker: str
    field_name: str
    pattern: re.Pattern[str]
    value: Callable[[re.Match[str]], object]
    unit: str
    rationale: str
    scope_type: str = "COMPANY_CONSOLIDATED"
    scope_period: str | None = None
    denominator_basis: str | None = None
    formula: str | None = None
    derivation_components: dict[str, float] | None = None
    is_derived: bool = False
    extraction_match_confidence: float = 0.95
    semantic_scope_confidence: float = 0.90
    denominator_confidence: float = 1.0


def _pct(value: str) -> float:
    return round(float(value.replace(".", "").replace(",", ".")) / 100, 6)


def _number(value: str) -> float:
    return float(value.replace(" ", "").replace(".", "").replace(",", "."))


_RULES = (
    ExtractionRule(
        "AZUL4", "commodity_roles",
        re.compile(r"Combustível de aviação.{0,100}R\$\s*1[.]341,0 milhões", re.I),
        lambda _: {"OIL": "CONSUMER"}, "role",
        "Issuer discloses aviation fuel as an operating cost.",
    ),
    ExtractionRule(
        "AZUL4", "commodity_hedges",
        re.compile(r"hedge em aproximadamente\s*(?P<pct>\d+[,.]\d+)\s*% de seu consumo esperado de combustível", re.I),
        lambda match: {"OIL": _pct(match["pct"])}, "share",
        "Explicit share of expected fuel consumption hedged for the next twelve months.",
        scope_type="COMMODITY_CONSUMPTION_HEDGE", scope_period="NEXT_12_MONTHS",
        denominator_basis="EXPECTED_FUEL_CONSUMPTION",
    ),
    ExtractionRule(
        "ELET3", "commodity_roles",
        re.compile(r"Preço Médio de Contratos de Venda.{0,300}Energia Descontratada", re.I),
        lambda _: {"ELECTRICITY_PRICE": "PRODUCER"}, "role",
        "Issuer discloses contracted sales and uncontracted electricity volumes.",
    ),
    ExtractionRule(
        "KLBN11", "foreign_currency_debt_pct",
        re.compile(r"62%\s+38%\s+BRL\s+USD\s+Dívida em Dólar", re.I),
        lambda _: 0.38, "share_of_gross_debt",
        "Consolidated debt currency chart states 62% BRL and 38% USD.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "KLBN11", "floating_rate_debt_pct",
        re.compile(r"25%\s+75%\s+SOFR\s+USD Fixo\s+5%\s+93%\s+IPCA\s+CDI\s+2%\s+Outros\s+62%\s+38%\s+BRL\s+USD", re.I),
        lambda _: round(0.38 * 0.25 + 0.62 * 0.93, 4), "share_of_gross_debt",
        "Derived from disclosed currency and indexer mix: USD×SOFR plus BRL×CDI.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="usd_share*sofr_within_usd + brl_share*cdi_within_brl",
        derivation_components={
            "usd_share": 0.38, "sofr_within_usd": 0.25,
            "brl_share": 0.62, "cdi_within_brl": 0.93,
        },
        is_derived=True,
        extraction_match_confidence=0.90,
        semantic_scope_confidence=0.90,
        denominator_confidence=0.90,
    ),
    ExtractionRule(
        "KLBN11", "inflation_linked_debt_pct",
        re.compile(r"25%\s+75%\s+SOFR\s+USD Fixo\s+5%\s+93%\s+IPCA\s+CDI\s+2%\s+Outros\s+62%\s+38%\s+BRL\s+USD", re.I),
        lambda _: round(0.62 * 0.05, 4), "share_of_gross_debt",
        "Derived from disclosed currency and indexer mix: BRL share × IPCA share.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="brl_share*ipca_within_brl",
        derivation_components={"brl_share": 0.62, "ipca_within_brl": 0.05},
        is_derived=True,
        extraction_match_confidence=0.90,
        semantic_scope_confidence=0.90,
        denominator_confidence=0.90,
    ),
    ExtractionRule(
        "PETR4", "commodity_roles",
        re.compile(r"recordes de produção total operada", re.I),
        lambda _: {"OIL": "PRODUCER"}, "role",
        "Issuer explicitly reports operated oil and gas production.",
    ),
    ExtractionRule(
        "PETR4", "commodity_production",
        re.compile(r"produção total operada\s*\((?P<value>\d+[,.]\d+)\s*MM boed\)", re.I),
        lambda match: {"OIL_MMBOED": _number(match["value"])}, "MMboed",
        "Explicit operated production in the results presentation.",
        scope_type="OPERATED_PRODUCTION", scope_period="1Q26",
        denominator_basis="OPERATED_VOLUME",
        semantic_scope_confidence=0.98,
    ),
    ExtractionRule(
        "PRIO3", "commodity_roles",
        re.compile(r"exploração e produção de óleo e gás natural", re.I),
        lambda _: {"OIL": "PRODUCER"}, "role",
        "Issuer describes its business as oil and natural-gas exploration and production.",
    ),
    ExtractionRule(
        "PRIO3", "commodity_production",
        re.compile(r"Produção média de\s*(?P<value>\d+\s*,\s*\d+)\s*kbpd no 1\s*T\s*26", re.I),
        lambda match: {"OIL_KBPD_DISCLOSED_ASSET": _number(match["value"])}, "kbpd",
        "Explicit asset production; kept distinct from consolidated production.",
        scope_type="ASSET_PRODUCTION", scope_period="1Q26",
        denominator_basis="DISCLOSED_ASSET_VOLUME",
        semantic_scope_confidence=0.98,
    ),
    ExtractionRule(
        "RECV3", "commodity_roles",
        re.compile(r"Dated Brent.{0,120}Produção \(kboed\)", re.I),
        lambda _: {"OIL": "PRODUCER"}, "role",
        "Issuer presents production directly alongside the Dated Brent benchmark.",
    ),
    ExtractionRule(
        "RECV3", "commodity_production",
        re.compile(r"Produção \(kboed\)\s*25,0\s*(?P<value>24,4)", re.I),
        lambda match: {"OIL_KBOED": _number(match["value"])}, "kboed",
        "Explicit 1Q26 production from the issuer presentation.",
        scope_type="COMPANY_PRODUCTION", scope_period="1Q26",
        denominator_basis="COMPANY_REPORTED_VOLUME",
    ),
    ExtractionRule(
        "SLCE3", "foreign_currency_debt_pct",
        re.compile(
            r"Subtotal Endividamento USD.{0,80}(?P<usd>141[.]888).*?"
            r"Subtotal Endividamento Geral.{0,80}(?P<total>7[.]318[.]579)",
            re.I,
        ),
        lambda match: round(_number(match["usd"]) / _number(match["total"]), 6),
        "share_of_gross_debt",
        "Derived from explicitly disclosed USD and total gross debt subtotals.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="usd_debt/total_gross_debt",
        derivation_components={"usd_debt": 141888, "total_gross_debt": 7318579},
        is_derived=True,
        extraction_match_confidence=0.92,
        semantic_scope_confidence=0.95,
        denominator_confidence=0.95,
    ),
    ExtractionRule(
        "SLCE3", "commodity_roles",
        re.compile(r"100% plantado\s+100% plantado\s+100% colhido\s+Algodão\s+Milho\s+Soja", re.I),
        lambda _: {"SOY": "PRODUCER", "CORN": "PRODUCER", "COTTON": "PRODUCER"},
        "role", "Issuer reports planted/harvest status for its three crops.",
    ),
    ExtractionRule(
        "SLCE3", "currency_hedges",
        re.compile(r"Hedge de câmbio – Soja.{0,100}%\s*(?P<pct>74,9)", re.I),
        lambda match: {"SOY_2025_26": _pct(match["pct"])}, "share",
        "Explicit FX hedge share for the 2025/26 soybean crop.",
        scope_type="COMMODITY_CROP_HEDGE", scope_period="2025/26",
        denominator_basis="EXPECTED_SOY_CROP",
        semantic_scope_confidence=0.98,
    ),
    ExtractionRule(
        "SLCE3", "commodity_hedges",
        re.compile(r"Hedge de Commodity – Soja.{0,100}%\s*(?P<pct>75,1)", re.I),
        lambda match: {"SOY_2025_26": _pct(match["pct"])}, "share",
        "Explicit commodity hedge share for the 2025/26 soybean crop.",
        scope_type="COMMODITY_CROP_HEDGE", scope_period="2025/26",
        denominator_basis="EXPECTED_SOY_CROP",
        semantic_scope_confidence=0.98,
    ),
    ExtractionRule(
        "SUZB3", "commodity_roles",
        re.compile(r"Produção totalmente vendida.{0,100}preços de celulose", re.I),
        lambda _: {"PULP": "PRODUCER"}, "role",
        "Issuer explicitly states pulp production was fully sold.",
    ),
    ExtractionRule(
        "VALE3", "commodity_roles",
        re.compile(r"Ser o maior produtor global de minério de ferro", re.I),
        lambda _: {"IRON_ORE": "PRODUCER"}, "role",
        "Issuer explicitly describes itself as a global iron-ore producer.",
    ),
    ExtractionRule(
        "VALE3", "commodity_production",
        re.compile(r"Produção de Min[.] de ferro \(Mt\)\s*68\s*(?P<value>70)\s*1T25\s*1T26", re.I),
        lambda match: {"IRON_ORE_MT": _number(match["value"])}, "Mt",
        "Explicit 1Q26 iron-ore production.",
        scope_type="COMPANY_PRODUCTION", scope_period="1Q26",
        denominator_basis="COMPANY_REPORTED_VOLUME",
    ),
)


class CompanyMacroExposureExtractor:
    """Extract only whitelisted, reviewable disclosures from one selection run."""

    methodology_version = "4C-exposure-rules-v1"

    def __init__(self, store: DatabaseStore) -> None:
        self.store = store
        self._ensure_table()

    def extract(self, selection_run_id: str) -> dict[str, object]:
        self.store.connection.execute(
            """
            UPDATE company_macro_exposure_facts SET is_active=FALSE
            WHERE selection_run_id=?
            """,
            [selection_run_id],
        )
        documents = self._documents(selection_run_id)
        facts: list[dict[str, object]] = []
        for document in documents:
            normalized_text = re.sub(r"\s+", " ", document.text)
            for rule in _RULES:
                if rule.ticker != document.ticker:
                    continue
                match = rule.pattern.search(normalized_text)
                if not match:
                    continue
                value = rule.value(match)
                excerpt = normalized_text[
                    max(0, match.start() - 100):min(len(normalized_text), match.end() + 100)
                ]
                confidence = round(
                    0.35 * rule.extraction_match_confidence
                    + 0.30 * rule.semantic_scope_confidence
                    + 0.20 * rule.denominator_confidence,
                    4,
                )
                evidence = ExposureFieldEvidence(
                    field_name=rule.field_name,
                    value=value,
                    source_type=f"CVM_IPE_{document.classification}",
                    evidence_id=document.document_id,
                    document_version=document.version,
                    evidence_excerpt=excerpt,
                    page_number=None,
                    raw_value=match.group(0),
                    unit=rule.unit,
                    normalized_value=value,
                    scope_entity=document.ticker,
                    scope_type=rule.scope_type,
                    scope_period=rule.scope_period,
                    denominator_basis=rule.denominator_basis,
                    formula=rule.formula,
                    derivation_components=rule.derivation_components,
                    available_at=document.available_at.replace(tzinfo=timezone.utc),
                    extraction_method=(
                        ExtractionMethod.RULE_DERIVED
                        if rule.is_derived
                        else ExtractionMethod.EXPLICIT_DISCLOSURE
                    ),
                    methodology_version=self.methodology_version,
                    confidence=confidence,
                    extraction_match_confidence=rule.extraction_match_confidence,
                    semantic_scope_confidence=rule.semantic_scope_confidence,
                    denominator_confidence=rule.denominator_confidence,
                    review_confidence=0,
                    is_estimated=False,
                    rationale=rule.rationale,
                )
                fact = self._save(document.ticker, selection_run_id, evidence)
                facts.append(fact)
        coverage = self.coverage(selection_run_id)
        return {
            "selection_run_id": selection_run_id,
            "facts_extracted": len(facts),
            "coverage": coverage,
            "facts": facts,
        }

    def coverage(self, selection_run_id: str) -> list[dict[str, object]]:
        rows = self.store.connection.execute(
            """
            WITH pilot AS (
                SELECT DISTINCT ticker FROM company_ticker_map
                WHERE review_status='VALIDATED'
            ), facts AS (
                SELECT ticker,COUNT(DISTINCT field_name) AS known_fields
                FROM company_macro_exposure_facts
                WHERE selection_run_id=? AND is_active=TRUE
                GROUP BY ticker
            )
            SELECT p.ticker,COALESCE(f.known_fields,0)
            FROM pilot p LEFT JOIN facts f USING(ticker)
            ORDER BY p.ticker
            """,
            [selection_run_id],
        ).fetchall()
        return [
            {
                "ticker": ticker,
                "known_fields": count,
                "meets_three": count >= 3,
                "blocker": None if count >= 3 else "INSUFFICIENT_EXPLICIT_DISCLOSURES",
            }
            for ticker, count in rows
        ]

    def _documents(self, selection_run_id: str) -> list[SourceDocument]:
        rows = self.store.connection.execute(
            """
            SELECT s.ticker,s.document_id,s.version,s.delivery_date,s.classification,
                   e.extracted_text
            FROM company_exposure_document_selections s
            JOIN extracted_documents e
              ON e.document_id=s.document_id
             AND e.document_checksum=s.document_checksum
            WHERE s.selection_run_id=?
              AND s.delivery_date <= (
                SELECT MAX(delivery_date) FROM company_exposure_document_selections
                WHERE selection_run_id=?
              )
            """,
            [selection_run_id, selection_run_id],
        ).fetchall()
        return [SourceDocument(*row) for row in rows]

    def _ensure_table(self) -> None:
        self.store.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS company_macro_exposure_facts (
                fact_id VARCHAR PRIMARY KEY,
                selection_run_id VARCHAR NOT NULL,
                ticker VARCHAR NOT NULL,
                field_name VARCHAR NOT NULL,
                normalized_value VARCHAR NOT NULL,
                evidence_payload VARCHAR NOT NULL,
                methodology_version VARCHAR NOT NULL,
                review_status VARCHAR NOT NULL DEFAULT 'HUMAN_REVIEW_PENDING',
                reviewed_by VARCHAR,
                reviewed_at TIMESTAMP,
                review_decision VARCHAR,
                review_notes VARCHAR,
                source_excerpt_hash VARCHAR,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        migrations = {
            "review_status": "VARCHAR DEFAULT 'HUMAN_REVIEW_PENDING'",
            "reviewed_by": "VARCHAR",
            "reviewed_at": "TIMESTAMP",
            "review_decision": "VARCHAR",
            "review_notes": "VARCHAR",
            "source_excerpt_hash": "VARCHAR",
            "is_active": "BOOLEAN DEFAULT TRUE",
        }
        existing_columns = {
            row[1] for row in self.store.connection.execute(
                "PRAGMA table_info('company_macro_exposure_facts')"
            ).fetchall()
        }
        for column, column_type in migrations.items():
            if column not in existing_columns:
                self.store.connection.execute(
                    f"ALTER TABLE company_macro_exposure_facts "
                    f"ADD COLUMN {column} {column_type}"
                )
        self.store.connection.execute(
            """
            UPDATE company_macro_exposure_facts
            SET review_status='HUMAN_REVIEW_PENDING',
                reviewed_by=NULL,reviewed_at=NULL,review_decision=NULL,review_notes=NULL
            WHERE review_status='HUMAN_REVIEWED'
            """
        )

    def _save(
        self, ticker: str, selection_run_id: str, evidence: ExposureFieldEvidence
    ) -> dict[str, object]:
        identity = (
            f"{selection_run_id}|{ticker}|{evidence.field_name}|"
            f"{evidence.evidence_id}|{evidence.document_version}|{evidence.normalized_value}"
        )
        fact_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        payload = evidence.model_dump(mode="json")
        excerpt_hash = hashlib.sha256(
            (evidence.evidence_excerpt or "").encode()
        ).hexdigest()
        self.store.connection.execute(
            """
            INSERT INTO company_macro_exposure_facts (
                fact_id,selection_run_id,ticker,field_name,normalized_value,
                evidence_payload,methodology_version,review_status,reviewed_by,
                reviewed_at,review_decision,review_notes,source_excerpt_hash,
                is_active,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fact_id) DO UPDATE SET
                normalized_value=excluded.normalized_value,
                evidence_payload=excluded.evidence_payload,
                methodology_version=excluded.methodology_version,
                source_excerpt_hash=excluded.source_excerpt_hash,
                is_active=TRUE,
                review_status=CASE
                    WHEN company_macro_exposure_facts.source_excerpt_hash=
                         excluded.source_excerpt_hash
                     AND company_macro_exposure_facts.review_status IN
                         ('HUMAN_APPROVED','HUMAN_REJECTED')
                    THEN company_macro_exposure_facts.review_status
                    ELSE 'HUMAN_REVIEW_PENDING'
                END,
                reviewed_by=CASE
                    WHEN company_macro_exposure_facts.source_excerpt_hash=
                         excluded.source_excerpt_hash
                    THEN company_macro_exposure_facts.reviewed_by ELSE NULL END,
                reviewed_at=CASE
                    WHEN company_macro_exposure_facts.source_excerpt_hash=
                         excluded.source_excerpt_hash
                    THEN company_macro_exposure_facts.reviewed_at ELSE NULL END,
                review_decision=CASE
                    WHEN company_macro_exposure_facts.source_excerpt_hash=
                         excluded.source_excerpt_hash
                    THEN company_macro_exposure_facts.review_decision ELSE NULL END,
                review_notes=CASE
                    WHEN company_macro_exposure_facts.source_excerpt_hash=
                         excluded.source_excerpt_hash
                    THEN company_macro_exposure_facts.review_notes ELSE NULL END
            """,
            [
                fact_id, selection_run_id, ticker, evidence.field_name,
                json.dumps(evidence.normalized_value, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False), self.methodology_version,
                "HUMAN_REVIEW_PENDING", None, None, None, None, excerpt_hash, True,
                datetime.now(timezone.utc).replace(tzinfo=None),
            ],
        )
        return {"fact_id": fact_id, "ticker": ticker, **payload}
