from datetime import datetime, timezone

from macro_b3_bot.adapters.bcb.normalizer import compute_raw_checksum
from macro_b3_bot.application.extract_ipe_documents import IpeExtractionPipeline
from macro_b3_bot.application.ingest_company_exposure_documents import (
    ExposureDocumentClass,
    classify_exposure_document,
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
    ) == ExposureDocumentClass.DEBT_DISCLOSURE
    assert classify_exposure_document(
        "Comunicado ao Mercado", "Outros", "Relatório anual Form 20-F de 2025"
    ) == ExposureDocumentClass.ANNUAL_REPORT
    assert classify_exposure_document(
        "Dados Econômico-Financeiros", "FRE", "Formulário de Referência 2026"
    ) == ExposureDocumentClass.REFERENCE_FORM


def test_kabin_consolidated_debt_mix_rules_are_factor_specific() -> None:
    text = (
        "25% 75% SOFR USD Fixo 5% 93% IPCA CDI 2% Outros "
        "62% 38% BRL USD Dívida em Dólar"
    )
    values = {}
    for rule in _RULES:
        if rule.ticker != "KLBN11":
            continue
        match = rule.pattern.search(text)
        assert match is not None
        values[rule.field_name] = rule.value(match)
    assert values == {
        "foreign_currency_debt_pct": 0.38,
        "floating_rate_debt_pct": 0.6716,
        "inflation_linked_debt_pct": 0.031,
    }


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
