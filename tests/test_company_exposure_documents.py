from datetime import datetime, timezone
import io
from zipfile import ZipFile

from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum
from macro_b3_bot.application.extract_ipe_documents import IpeExtractionPipeline
from macro_b3_bot.application.ingest_company_exposure_documents import (
    ExposureDocumentClass,
    classify_exposure_document,
)
from macro_b3_bot.application.ingest_company_fre_documents import (
    CompanyFreDocumentPipeline,
)
from macro_b3_bot.application.extract_company_macro_exposures import _RULES
from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_financial_result_classification_requires_financial_metadata() -> None:
    assert classify_exposure_document(
        "Dados Econômico-Financeiros",
        "Press-release",
        "Divulgação dos resultados do 1T26",
    ) == ExposureDocumentClass.EARNINGS_RELEASE
    assert classify_exposure_document(
        "Aviso aos Acionistas",
        "Outros avisos",
        "Resultado do Leilão de Frações",
    ) == ExposureDocumentClass.CORPORATE_ACTION


def test_results_presentation_and_esg_are_distinct() -> None:
    assert classify_exposure_document(
        "Comunicado ao Mercado",
        "Apresentações a analistas/agentes do mercado",
        "Apresentação de Resultados do 4T25",
    ) == ExposureDocumentClass.RESULTS_PRESENTATION
    assert classify_exposure_document(
        "Comunicado ao Mercado",
        "Apresentações a analistas/agentes do mercado",
        "Webinar ESG",
    ) == ExposureDocumentClass.ESG


def test_debt_annual_and_reference_form_families() -> None:
    assert classify_exposure_document(
        "Dados Econômico-Financeiros", "Outros", "Composição do endividamento"
    ) == ExposureDocumentClass.DEBT_INSTRUMENT_DISCLOSURE
    assert classify_exposure_document(
        "Comunicado ao Mercado", "Outros", "Relatório anual Form 20-F de 2025"
    ) == ExposureDocumentClass.ISSUER_ANNUAL_REPORT
    assert classify_exposure_document(
        "Dados Econômico-Financeiros", "FRE", "Formulário de Referência 2026"
    ) == ExposureDocumentClass.REFERENCE_FORM_DOCUMENT


def test_fiduciary_report_and_reference_notice_are_not_issuer_documents() -> None:
    assert classify_exposure_document(
        "Dados Econômico-Financeiros",
        "Relatório de Agente Fiduciário",
        "Relatório Anual do Agente Fiduciário 2025",
    ) == ExposureDocumentClass.FIDUCIARY_REPORT
    assert classify_exposure_document(
        "Comunicado ao Mercado",
        "Outros",
        "Petrobras arquiva Formulário de Referência 2026",
    ) == ExposureDocumentClass.REFERENCE_FORM_NOTICE


def test_kabin_consolidated_debt_mix_rules_are_factor_specific() -> None:
    text = (
        "25% 75% SOFR USD Fixo 5% 93% IPCA CDI 2% Outros "
        "62% 38% BRL USD Dívida em Dólar"
    )
    values = {}
    for rule in _RULES:
        if rule.ticker != "KLBN11" or rule.field_name not in {
            "foreign_currency_debt_pct",
            "floating_rate_debt_pct",
            "inflation_linked_debt_pct",
        }:
            continue
        match = rule.pattern.search(text)
        assert match is not None
        values[rule.field_name] = rule.value(match)
    assert values == {
        "foreign_currency_debt_pct": 0.38,
        "floating_rate_debt_pct": 0.6716,
        "inflation_linked_debt_pct": 0.031,
    }


def test_fre_rules_preserve_denominator_and_sector_specific_semantics() -> None:
    samples = {
        ("EQTL3", "floating_rate_debt_pct"): (
            "Em 31 de dezembro de 2025 a Companhia possuía "
            "69,1% de seu endividamento atrelado ao CDI.",
            0.691,
        ),
        ("ITUB4", "bank_retail_credit_portfolio_pct"): (
            "Negócios de Varejo, representando 61% da nossa carteira "
            "de crédito em 2025.",
            0.61,
        ),
        ("SUZB3", "export_revenue_pct"): (
            "cerca de 80% da receita líquida da Companhia é proveniente "
            "de exportações com preços em Dólares Norte-Americano",
            0.8,
        ),
        ("VALE3", "floating_rate_debt_pct"): (
            "cerca de 61% do saldo de empréstimos e financiamentos estava "
            "atrelado à taxas de juros flutuantes",
            0.61,
        ),
        ("RECV3", "debt_instrument_durations"): (
            'custo médio dolarizado de 5,66% ao ano e "duration" '
            "aproximada de 5,2 anos",
            {"DISCLOSED_HEDGED_DEBENTURE": 5.2},
        ),
    }
    for (ticker, field_name), (text, expected) in samples.items():
        rule = next(
            item for item in _RULES
            if item.ticker == ticker and item.field_name == field_name
        )
        match = rule.pattern.search(text)
        assert match is not None
        assert rule.value(match) == expected
        assert rule.denominator_basis


def test_slc_currency_debt_share_uses_disclosed_subtotals() -> None:
    text = (
        "Subtotal Endividamento USD 7,5% 7,5% 206.948 141.888 "
        "Subtotal Endividamento Geral 15,1% 14,9% 7.779.679 7.318.579"
    )
    rule = next(
        item for item in _RULES
        if item.ticker == "SLCE3" and item.field_name == "foreign_currency_debt_pct"
    )
    match = rule.pattern.search(text)
    assert match is not None
    assert rule.value(match) == 0.019387


def test_extraction_batch_is_restricted_to_selected_document_ids(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    store = DatabaseStore(tmp_path / "audit.duckdb")
    now = datetime.now(timezone.utc)
    for document_id in ("selected", "unrelated"):
        path = tmp_path / f"{document_id}.txt"
        content = f"official text for {document_id}".encode()
        path.write_bytes(content)
        checksum = compute_raw_checksum(content)
        store.save_downloaded_document({
            "document_id": document_id, "source_url": f"https://example/{document_id}",
            "http_status": 200, "mime_type": "text/plain", "file_extension": "txt",
            "file_size_bytes": len(content), "raw_path": str(path),
            "document_checksum": checksum, "downloaded_at": now,
            "ingestion_run_id": "test",
        })
        store.connection.execute(
            """
            INSERT INTO ipe_processing_queue (
                document_id,status,priority_score,category_score,recency_score,
                ticker_mapping_score,liquidity_score,material_terms_score,
                attempts,created_at,updated_at
            ) VALUES (?, 'DOWNLOADED',0,0,0,0,0,0,0,?,?)
            """,
            [document_id, now, now],
        )
    store.close()

    result = IpeExtractionPipeline(settings).extract_downloaded_batch(
        document_ids=["selected"]
    )
    store = DatabaseStore(tmp_path / "audit.duckdb")
    statuses = dict(store.connection.execute(
        "SELECT document_id,status FROM ipe_processing_queue"
    ).fetchall())
    store.close()
    assert result == {"total_processed": 1, "extracted_count": 1}
    assert statuses == {"selected": "EXTRACTED", "unrelated": "DOWNLOADED"}


def test_fre_selection_uses_latest_version_available_at_cutoff(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    statement_dir = tmp_path / "raw" / "cvm" / "statements"
    statement_dir.mkdir(parents=True)
    csv_text = (
        "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC\n"
        "00;2026-12-31;1;Test;1;FRE;10;2026-05-01;http://www.rad.cvm.gov.br/10\n"
        "00;2026-12-31;2;Test;1;FRE;11;2026-06-01;http://www.rad.cvm.gov.br/11\n"
        "00;2026-12-31;3;Test;1;FRE;12;2026-08-01;http://www.rad.cvm.gov.br/12\n"
    )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("fre_cia_aberta_2026.csv", csv_text.encode("latin1"))
    (statement_dir / "fre_cia_aberta_2026.zip").write_bytes(buffer.getvalue())
    store = DatabaseStore(tmp_path / "audit.duckdb")
    store.connection.execute(
        """
        INSERT INTO company_ticker_map (
            ticker,cvm_code,cnpj,mapping_source,confidence,validated,created_at,
            legal_name,valid_from,valid_to,review_status,evidence_id,mapping_version
        ) VALUES ('TEST3','1','00','test',1,TRUE,TIMESTAMP '2026-01-01',
                  'Test',DATE '2026-01-01',NULL,'VALIDATED','test','v1')
        """
    )
    selected = CompanyFreDocumentPipeline(settings)._select_latest(
        store, datetime(2026, 7, 1)
    )
    store.close()
    assert len(selected) == 1
    assert selected[0]["document_id"] == "11"
    assert selected[0]["version"] == 2
    assert str(selected[0]["source_url"]).startswith("https://")
