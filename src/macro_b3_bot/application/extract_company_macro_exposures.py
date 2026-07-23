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
        "AZUL4", "foreign_currency_debt_pct",
        re.compile(
            r"Empréstimos e financiamentos\s+23[.]059[.]604.*?"
            r"Exposição ao US[$].{0,500}Empréstimos e financiamentos "
            r"\(21[.]818[.]077\)",
            re.I,
        ),
        lambda _: round(21_818_077 / 23_059_604, 6), "share_of_loans",
        "Derived from FRE total loans and the USD exposure table.",
        scope_type="CONTRACTUAL_USD_LOANS_BEFORE_HEDGE",
        denominator_basis="CONSOLIDATED_LOANS_AND_FINANCINGS",
        formula="usd_loans/total_loans_and_financings",
        derivation_components={
            "usd_loans": 21_818_077,
            "total_loans_and_financings": 23_059_604,
        },
        is_derived=True,
        extraction_match_confidence=0.92,
        semantic_scope_confidence=0.94,
        denominator_confidence=0.95,
    ),
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
        "BBAS3", "bank_market_risk_sensitivities",
        re.compile(
            r"Análise de sensibilidade para o conjunto de operações.{0,300}"
            r"Cenário I.{0,180}Taxa Pré-fixada.{0,120}\(12[.]657[.]515\).*?"
            r"Cupons de Taxas de Juros.{0,120}\(11[.]489[.]079\).*?"
            r"Cupons de Índices de Preços.{0,120}\(457[.]960\).*?"
            r"Cupons de Moedas Estrangeiras.{0,120}\(2[.]810[.]805\)",
            re.I,
        ),
        lambda _: {
            "PREFX_100BP_LOSS_BRL_THOUSAND": -12_657_515,
            "INTEREST_COUPON_100BP_LOSS_BRL_THOUSAND": -11_489_079,
            "INFLATION_COUPON_100BP_LOSS_BRL_THOUSAND": -457_960,
            "FX_COUPON_100BP_LOSS_BRL_THOUSAND": -2_810_805,
        },
        "BRL_thousand",
        "FRE discloses banking-book plus trading-book 100bp sensitivity by factor.",
        scope_type="BANKING_AND_TRADING_BOOK_MARKET_RISK",
        scope_period="FY2025",
        denominator_basis="SCENARIO_I_100_BASIS_POINTS",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.99,
    ),
    ExtractionRule(
        "BBAS3", "bank_credit_market_share",
        re.compile(
            r"possui\s*(?P<pct>16,4)%\s+de participação de mercado "
            r"na carteira de crédito",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "market_share",
        "FRE explicitly states Banco do Brasil's credit-portfolio market share.",
        scope_type="BRAZIL_CREDIT_MARKET_SHARE",
        scope_period="2025-12",
        denominator_basis="BRAZIL_CREDIT_MARKET",
        extraction_match_confidence=0.99,
        semantic_scope_confidence=0.99,
        denominator_confidence=0.96,
    ),
    ExtractionRule(
        "BBAS3", "bank_agribusiness_funding_market_share",
        re.compile(
            r"(?P<pct>43,0)%\s+nas letras de.{0,180}"
            r"crédito do agronegócio do Sistema Financeiro Nacional",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "market_share",
        "FRE explicitly states market share in Brazilian agribusiness credit letters.",
        scope_type="BRAZIL_AGRIBUSINESS_CREDIT_LETTER_MARKET_SHARE",
        scope_period="2025-12",
        denominator_basis="BRAZIL_FINANCIAL_SYSTEM_LCA",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.96,
    ),
    ExtractionRule(
        "ELET3", "commodity_roles",
        re.compile(r"Preço Médio de Contratos de Venda.{0,300}Energia Descontratada", re.I),
        lambda _: {"ELECTRICITY_PRICE": "PRODUCER"}, "role",
        "Issuer discloses contracted sales and uncontracted electricity volumes.",
    ),
    ExtractionRule(
        "ELET3", "foreign_currency_debt_pct",
        re.compile(
            r"Total Moeda Nacional\s+61[.]034[.]561.*?"
            r"Total Moeda Estrangeira\s+13[.]261[.]203.*?"
            r"Total\s+74[.]295[.]764",
            re.I,
        ),
        lambda _: round(13_261_203 / 74_295_764, 6), "share_of_gross_debt",
        "Derived from the FRE consolidated gross-debt table by currency.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="foreign_currency_debt/total_gross_debt",
        derivation_components={
            "foreign_currency_debt": 13_261_203,
            "total_gross_debt": 74_295_764,
        },
        is_derived=True,
        extraction_match_confidence=0.96,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "ELET3", "floating_rate_debt_pct",
        re.compile(
            r"CDI Empréstimos, financiamentos e debêntures "
            r"\(42[.]532[.]019\)",
            re.I,
        ),
        lambda _: round(42_532_019 / 74_295_764, 6), "share_of_gross_debt",
        "Derived from the FRE CDI sensitivity balance and consolidated gross debt.",
        scope_type="CDI_LINKED_DEBT",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="cdi_linked_debt/total_gross_debt",
        derivation_components={
            "cdi_linked_debt": 42_532_019,
            "total_gross_debt": 74_295_764,
        },
        is_derived=True,
        extraction_match_confidence=0.96,
        semantic_scope_confidence=0.94,
        denominator_confidence=0.96,
    ),
    ExtractionRule(
        "ELET3", "inflation_linked_debt_pct",
        re.compile(
            r"IPCA Obrigações.{0,80}\(43[.]766[.]663\).*?"
            r"Empréstimos, financiamentos e debêntures "
            r"\(21[.]028[.]085\)",
            re.I,
        ),
        lambda _: round(21_028_085 / 74_295_764, 6), "share_of_gross_debt",
        "Derived only from the IPCA-linked debt row; statutory obligations are excluded.",
        scope_type="IPCA_LINKED_DEBT",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="ipca_linked_debt/total_gross_debt",
        derivation_components={
            "ipca_linked_debt": 21_028_085,
            "total_gross_debt": 74_295_764,
        },
        is_derived=True,
        extraction_match_confidence=0.96,
        semantic_scope_confidence=0.95,
        denominator_confidence=0.96,
    ),
    ExtractionRule(
        "EQTL3", "foreign_currency_debt_pct",
        re.compile(
            r"31 de dezembro de 2025.{0,120}Dívida Bruta.{0,80}"
            r"54,8 bilhões.{0,100}12,2%\s*\(R\$\s*6,7 bilhões\).*?"
            r"moeda estrangeira",
            re.I,
        ),
        lambda _: 0.122, "share_of_gross_debt",
        "FRE explicitly states the foreign-currency share of consolidated gross debt.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "EQTL3", "floating_rate_debt_pct",
        re.compile(
            r"31 de dezembro de 2025.{0,100}69,1%\s+de seu endividamento "
            r"atrelado ao CDI",
            re.I,
        ),
        lambda _: 0.691, "share_of_gross_debt",
        "FRE explicitly states the CDI-linked share of consolidated debt.",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.97,
        denominator_confidence=0.96,
    ),
    ExtractionRule(
        "EQTL3", "currency_hedge_pct",
        re.compile(
            r"obrigações em moeda estrangeira.{0,80}100%\s+protegidas "
            r"por instrumento de hedge em reais",
            re.I,
        ),
        lambda _: 1.0, "share_of_foreign_currency_obligations",
        "FRE explicitly states that foreign-currency obligations are fully hedged into BRL.",
        scope_type="FOREIGN_CURRENCY_OBLIGATIONS_HEDGE",
        denominator_basis="FOREIGN_CURRENCY_OBLIGATIONS",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.96,
        denominator_confidence=0.98,
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
        "KLBN11", "export_revenue_pct",
        re.compile(
            r"receita de exportação representou\s*(?P<pct>37)%\s+"
            r"da receita líquida total",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_net_revenue",
        "FRE explicitly states export revenue as a share of total net revenue.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_NET_REVENUE",
        extraction_match_confidence=0.99,
        semantic_scope_confidence=0.99,
        denominator_confidence=0.99,
    ),
    ExtractionRule(
        "ITUB4", "bank_foreign_currency_assets_pct",
        re.compile(
            r"Sensibilidade à taxa de câmbio.{0,250}Ativos?\s+"
            r"2[.]493[.]597\s+572[.]572\s+3[.]066[.]169\s+"
            r"(?P<pct>18,7)",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_total_assets",
        "FRE explicitly states foreign-currency-denominated/indexed asset share.",
        scope_type="BANK_FOREIGN_CURRENCY_ASSETS",
        scope_period="FY2025",
        denominator_basis="DISCLOSED_SENSITIVITY_TABLE_TOTAL_ASSETS",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "ITUB4", "bank_loan_book_foreign_currency_pct",
        re.compile(
            r"Operações de Crédito e Arrendamento Mercantil Financeiro\s+"
            r"743[.]262\s+340[.]536\s+1[.]083[.]798\s+(?P<pct>31,4)",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_loan_book",
        "FRE explicitly states foreign-currency-denominated/indexed loan-book share.",
        scope_type="BANK_LOAN_BOOK_FOREIGN_CURRENCY",
        scope_period="FY2025",
        denominator_basis="TOTAL_LOANS_AND_FINANCIAL_LEASES",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "ITUB4", "bank_retail_credit_portfolio_pct",
        re.compile(
            r"representando\s*(?P<pct>61)%\s+da nossa carteira de crédito em 2025",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_loan_book",
        "FRE explicitly states the retail-business share of the credit portfolio.",
        scope_type="BANK_RETAIL_CREDIT_PORTFOLIO",
        scope_period="FY2025",
        denominator_basis="TOTAL_CREDIT_PORTFOLIO",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.96,
        denominator_confidence=0.97,
    ),
    ExtractionRule(
        "LREN3", "financial_services_funding",
        re.compile(
            r"Financiamentos [–-] Operações Serviços Financeiros.{0,180}"
            r"totalizavam R[$]\s*(?P<value>379,9)\s+milhões",
            re.I,
        ),
        lambda match: _number(match["value"]) * 1_000_000, "BRL",
        "FRE explicitly states financial-services operation funding.",
        scope_type="FINANCIAL_SERVICES_FUNDING",
        scope_period="FY2025",
        denominator_basis="ABSOLUTE_DISCLOSED_BALANCE",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.99,
    ),
    ExtractionRule(
        "LREN3", "financial_services_funding_floating_pct",
        re.compile(
            r"Financiamentos [–-] operações serviços financeiros.{0,900}"
            r"Total\s+379,9\s+423,1\s+825,0",
            re.I,
        ),
        lambda _: 1.0, "share_of_financial_services_funding",
        "Every nonzero FY2025 financial-services funding line is explicitly CDI-linked.",
        scope_type="FINANCIAL_SERVICES_FUNDING",
        scope_period="FY2025",
        denominator_basis="TOTAL_FINANCIAL_SERVICES_FUNDING",
        formula="sum(cdi_linked_funding)/total_financial_services_funding",
        derivation_components={
            "cdi_106_3_pct": 169.2,
            "cdi_105_9_pct": 172.7,
            "cdi_104_2_pct": 21.1,
            "cdi_102_2_pct": 16.9,
            "total_financial_services_funding": 379.9,
        },
        is_derived=True,
        extraction_match_confidence=0.94,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "LREN3", "net_cash_position",
        re.compile(
            r"Endividamento Líquido negativo \(Caixa líquido\) de "
            r"R[$]\s*1[.]\s*522,9 milhões",
            re.I,
        ),
        lambda _: 1_522_900_000.0, "BRL",
        "FRE explicitly states a positive consolidated net-cash position.",
        scope_type="CONSOLIDATED_NET_CASH",
        scope_period="FY2025",
        denominator_basis="CASH_AND_INVESTMENTS_MINUS_GROSS_DEBT",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.99,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "MGLU3", "foreign_currency_debt_pct",
        re.compile(
            r"Financiamento Inovação de SOFR\s*[+]\s*3%\s*A[.]A[.].{0,80}"
            r"1[.]000[.]737.*?Total\s+4[.]944[.]536",
            re.I,
        ),
        lambda _: round(1_000_737 / 4_944_536, 6), "share_of_gross_debt",
        "Derived from the FRE financing table before the fair-value hedge.",
        scope_type="CONTRACTUAL_CURRENCY_BEFORE_HEDGE",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="usd_sofr_financing/total_gross_debt",
        derivation_components={
            "usd_sofr_financing": 1_000_737,
            "total_gross_debt": 4_944_536,
        },
        is_derived=True,
        extraction_match_confidence=0.94,
        semantic_scope_confidence=0.92,
        denominator_confidence=0.96,
    ),
    ExtractionRule(
        "MGLU3", "floating_rate_debt_pct",
        re.compile(
            r"Debêntures\s+100%\s+do CDI.{0,100}3[.]929[.]623.*?"
            r"Financiamento Inovação.{0,180}100%\s+do CDI\s*[+]\s*1,75.*?"
            r"Total\s+4[.]944[.]536",
            re.I,
        ),
        lambda _: 1.0, "share_of_gross_debt_after_hedge",
        "All material debt in the FRE table is CDI-linked after the disclosed swap.",
        scope_type="ECONOMIC_EXPOSURE_AFTER_HEDGE",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="(cdi_debentures+usd_financing_swapped_to_cdi)/total_gross_debt",
        derivation_components={
            "cdi_debentures": 3_929_623,
            "usd_financing_swapped_to_cdi": 1_000_737,
            "fair_value_hedge_adjustment": 14_176,
            "total_gross_debt": 4_944_536,
        },
        is_derived=True,
        extraction_match_confidence=0.90,
        semantic_scope_confidence=0.90,
        denominator_confidence=0.94,
    ),
    ExtractionRule(
        "MGLU3", "currency_hedge_pct",
        re.compile(
            r"todos os seus passivos financeiros relevantes registrados "
            r"em moeda estrangeira.{0,100}operações de [“\"]swap",
            re.I,
        ),
        lambda _: 1.0, "share_of_disclosed_usd_financing",
        "The FRE states all relevant foreign-currency financial liabilities are swapped.",
        scope_type="RELEVANT_FOREIGN_CURRENCY_FINANCIAL_LIABILITIES_HEDGE",
        denominator_basis="RELEVANT_FOREIGN_CURRENCY_FINANCIAL_LIABILITIES",
        extraction_match_confidence=0.96,
        semantic_scope_confidence=0.92,
        denominator_confidence=0.96,
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
        "PETR4", "commodity_exports",
        re.compile(
            r"exportações de petróleo.{0,80}atingiram\s*"
            r"(?P<value>999)\s*mbpd no 4T25",
            re.I,
        ),
        lambda match: {"OIL_MBPD": _number(match["value"])}, "mbpd",
        "FRE explicitly reports quarterly petroleum export volume.",
        scope_type="COMPANY_PETROLEUM_EXPORT_VOLUME",
        scope_period="4Q25",
        denominator_basis="COMPANY_REPORTED_EXPORT_VOLUME",
        extraction_match_confidence=0.99,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
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
        "PRIO3", "debt_instrument_durations",
        re.compile(
            r"custo médio dolarizado de 6,59% a[.]a[.] e duration "
            r"aproximada de\s*(?P<years>4,4)\s*anos",
            re.I,
        ),
        lambda match: {"SENIOR_NOTES_OCT_2025": _number(match["years"])}, "years",
        "FRE explicitly states duration for the October 2025 senior notes.",
        scope_type="SPECIFIC_DEBT_INSTRUMENT",
        scope_period="FY2025",
        denominator_basis="SENIOR_NOTES_OCT_2025",
        extraction_match_confidence=0.96,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.99,
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
        "RECV3", "debt_instrument_durations",
        re.compile(
            r"custo médio dolarizado de 5,66% ao ano e [“\"]duration[”\"] "
            r"aproximada de\s*(?P<years>5,2)\s*anos",
            re.I,
        ),
        lambda match: {"DISCLOSED_HEDGED_DEBENTURE": _number(match["years"])}, "years",
        "FRE explicitly states duration for the disclosed swapped debenture.",
        scope_type="SPECIFIC_DEBT_INSTRUMENT",
        scope_period="FY2025",
        denominator_basis="DISCLOSED_HEDGED_DEBENTURE",
        extraction_match_confidence=0.97,
        semantic_scope_confidence=0.97,
        denominator_confidence=0.99,
    ),
    ExtractionRule(
        "RAIL3", "debt_duration_years",
        re.compile(
            r"dívida da Companhia apresentava.{0,100}prazo médio "
            r"\(duration\) de\s*(?P<years>5)\s*anos",
            re.I,
        ),
        lambda match: _number(match["years"]), "years",
        "FRE explicitly states the weighted average debt duration.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        extraction_match_confidence=0.99,
        semantic_scope_confidence=0.99,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "RAIL3", "inflation_linked_debt_pct",
        re.compile(
            r"ACF IPCA\s*[+]\s*6,48%.{0,40}494[.]225.*?"
            r"BNDES \(Finem\) IPCA.{0,40}27[.]050.*?"
            r"CCB.{0,80}IPCA.{0,40}814[.]423.*?"
            r"Debêntures \(Lei 12[.]431\) IPCA.{0,40}14[.]906[.]454.*?"
            r"Total\s+23[.]123[.]837",
            re.I,
        ),
        lambda _: round(
            (494_225 + 27_050 + 814_423 + 14_906_454) / 23_123_837, 6
        ),
        "share_of_gross_debt",
        "Derived from all explicitly listed IPCA-linked instruments in the FRE table.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="sum(ipca_linked_instruments)/total_gross_debt",
        derivation_components={
            "acf_ipca": 494_225,
            "bndes_ipca": 27_050,
            "ccb_ipca": 814_423,
            "incentivized_debentures_ipca": 14_906_454,
            "total_gross_debt": 23_123_837,
        },
        is_derived=True,
        extraction_match_confidence=0.94,
        semantic_scope_confidence=0.96,
        denominator_confidence=0.97,
    ),
    ExtractionRule(
        "RAIL3", "fixed_rate_debt_pct",
        re.compile(
            r"Sênior Notes Pré-fixado\s+4,73%\s+5[.]145[.]878.*?"
            r"Total\s+23[.]123[.]837",
            re.I,
        ),
        lambda _: round(5_145_878 / 23_123_837, 6), "share_of_gross_debt",
        "Derived from the explicitly listed fixed-rate senior notes.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_GROSS_DEBT",
        formula="fixed_rate_senior_notes/total_gross_debt",
        derivation_components={
            "fixed_rate_senior_notes": 5_145_878,
            "total_gross_debt": 23_123_837,
        },
        is_derived=True,
        extraction_match_confidence=0.97,
        semantic_scope_confidence=0.97,
        denominator_confidence=0.98,
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
        "SUZB3", "export_revenue_pct",
        re.compile(
            r"cerca de\s*(?P<pct>80)%\s+da receita líquida da Companhia "
            r"é proveniente de exportações",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_net_revenue",
        "FRE explicitly states the approximate export share of company net revenue.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_NET_REVENUE",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.97,
    ),
    ExtractionRule(
        "SUZB3", "revenue_foreign_currency_pct",
        re.compile(
            r"cerca de\s*(?P<pct>80)%\s+da receita líquida da Companhia "
            r"é proveniente de exportações com preços em Dólares",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_net_revenue",
        "FRE explicitly links the approximate export revenue share to USD pricing.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_NET_REVENUE",
        extraction_match_confidence=0.98,
        semantic_scope_confidence=0.97,
        denominator_confidence=0.97,
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
    ExtractionRule(
        "VALE3", "foreign_currency_debt_pct",
        re.compile(
            r"14%\s+do saldo de empréstimos e financiamentos estava "
            r"denominado em reais, e os\s*(?P<pct>86)%\s+restantes "
            r"estavam denominados em outras moedas",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_borrowings",
        "FRE explicitly states the pre-swap non-BRL share of borrowings.",
        scope_type="CONTRACTUAL_CURRENCY_BEFORE_HEDGE",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_BORROWINGS_AND_FINANCINGS",
        extraction_match_confidence=0.99,
        semantic_scope_confidence=0.96,
        denominator_confidence=0.98,
    ),
    ExtractionRule(
        "VALE3", "floating_rate_debt_pct",
        re.compile(
            r"cerca de\s*(?P<pct>61)%\s+do saldo de empréstimos e "
            r"financiamentos estava atrelado à taxas de juros flutuantes",
            re.I,
        ),
        lambda match: _pct(match["pct"]), "share_of_borrowings",
        "FRE explicitly states the floating-rate share of borrowings.",
        scope_period="FY2025",
        denominator_basis="CONSOLIDATED_BORROWINGS_AND_FINANCINGS",
        extraction_match_confidence=0.99,
        semantic_scope_confidence=0.98,
        denominator_confidence=0.98,
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
            WITH cutoff AS (
                SELECT MAX(delivery_date) AS as_of
                FROM company_exposure_document_selections
                WHERE selection_run_id=?
            )
            SELECT s.ticker,s.document_id,s.version,s.delivery_date,s.classification,
                   e.extracted_text
              FROM company_exposure_document_selections s
              JOIN extracted_documents e
                ON e.document_id=s.document_id
               AND e.document_checksum=s.document_checksum
             WHERE s.selection_run_id=?
               AND s.delivery_date <= (SELECT as_of FROM cutoff)
            UNION ALL
            SELECT f.ticker,
                   'FRE:' || f.document_id,
                   f.version,MAX(f.available_at),
                   'FRE_EXPOSURE_SECTIONS',
                   STRING_AGG(f.extracted_text,' ')
              FROM company_fre_sections f
             WHERE f.available_at <= (SELECT as_of FROM cutoff)
             GROUP BY f.ticker,f.document_id,f.version
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
