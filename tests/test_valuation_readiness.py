from datetime import date, datetime, timezone

from macro_b3_bot.application.valuation_readiness import ValuationReadinessGate
from macro_b3_bot.application.market_snapshot_pilot import PITMarketDataIngestor
from macro_b3_bot.adapters.b3_cotahist import B3CotahistReader
from macro_b3_bot.adapters.cvm_capital_composition import CVMCapitalCompositionReader
from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    BridgeReplayObservation,
    CashFlowNormalizationAdjustment,
    FinancialBaselineSnapshot,
    FinancialFieldEvidence,
    NormalizedCashFlowSnapshot,
    MarketSnapshotPIT,
)


AS_OF = datetime(2026, 7, 22, tzinfo=timezone.utc)


def _baseline() -> FinancialBaselineSnapshot:
    fields = []
    for name in (
        "ttm_revenue", "ttm_costs", "ttm_ebit", "ttm_financial_result",
        "ttm_pre_tax_income", "ttm_net_income", "ttm_operating_cash_flow",
        "ttm_capex", "ttm_fcf", "gross_debt", "cash", "net_debt",
        "average_gross_debt", "working_capital",
    ):
        fields.append(FinancialFieldEvidence(
            field_name=name, source_ids=[f"src-{name}"],
            source_locations=["cvm"], available_at=[AS_OF],
            period_end=date(2026, 3, 31), formula="reported", evidence_label="fact_source_reported", confidence=1,
        ))
    return FinancialBaselineSnapshot(
        baseline_id="base", ticker="MGLU3", cvm_code="1", as_of_timestamp=AS_OF,
        latest_quarter=date(2026, 3, 31), methodology_version="test",
        ttm_revenue=1000, ttm_costs=700, ttm_ebit=100, ttm_financial_result=-10,
        ttm_pre_tax_income=90, ttm_net_income=70, ttm_operating_cash_flow=120,
        ttm_capex=-40, ttm_fcf=80, gross_debt=200, cash=50, net_debt=150,
        average_gross_debt=210, working_capital=20, field_evidence=fields,
        confidence=.9, run_id="test", created_at=AS_OF,
    )


def _calibration() -> BridgeCalibrationResult:
    rows = [BridgeReplayObservation(
        ticker="MGLU3", bridge="FX", period_end=date(2025, 3, 31),
        factor_change=.1, financial_change=1, predicted_change=1, error=0,
        source_ids=["cvm"],
    ) for _ in range(5)]
    return BridgeCalibrationResult(
        calibration_id="cal", ticker="MGLU3", bridge="FX", mode="CALIBRATION_MODE",
        observations=rows, parameters={}, parameter_ranges={}, heuristic_sensitivity_band={},
        sensitivity_band_type="HEURISTIC_SENSITIVITY_BAND", mean_absolute_error=0,
        in_sample_mae=0, validation_method="EXPANDING_WINDOW_WALK_FORWARD",
        observation_count=5, calibration_type="EMPIRICAL_IN_SAMPLE", validation_gate_passed=True,
        confidence=.9, calibration_status="COMPANY_CALIBRATED", methodology_version="test", run_id="test",
    )


def _fcf() -> NormalizedCashFlowSnapshot:
    adjustment = CashFlowNormalizationAdjustment(
        adjustment_id="a", field_name="ocf", value=1, sign=1,
        period_end=date(2026, 3, 31), source_ids=["cvm"], rationale="test",
        recurrence="NORMALIZATION_PROXY", confidence=.5, formula="x",
    )
    return NormalizedCashFlowSnapshot(
        snapshot_id="fcf", ticker="MGLU3", as_of_timestamp=AS_OF,
        reported_operating_cash_flow=120, reported_capex=-40, levered_fcf_proxy=80,
        normalized_operating_cash_flow=100, maintenance_capex=-40, normalized_levered_fcf=60,
        statistical_normalized_fcf_proxy=60, normalization_type="STATISTICAL_NORMALIZATION_PROXY",
        normalization_status="NOT_VALUATION_READY", adjustments=[adjustment],
        methodology_version="test", confidence=.4, run_id="test",
    )


def test_gate_blocks_low_confidence_and_fcf_and_labels_multiples() -> None:
    result = ValuationReadinessGate().assess(
        baseline=_baseline(), calibrations=[_calibration().model_copy(update={"validation_gate_passed": False, "confidence": .2})],
        normalized_cash_flow=_fcf(), market_data={"price": 10, "shares_outstanding": 100},
    )
    assert result.status == "VALUATION_BLOCKED_LOW_CALIBRATION_CONFIDENCE"
    assert {"LOW_CALIBRATION_CONFIDENCE", "FCF_NOT_READY"} <= set(result.blockers)
    assert result.valuation_eligible is False and result.dcf_eligible is False
    assert result.descriptive_metrics["market_capitalization"].not_a_fair_value is True
    assert result.descriptive_metrics["pe_observed"].classification == "DESCRIPTIVE_ONLY"


def test_gate_flags_missing_market_data_and_persists_identity() -> None:
    result = ValuationReadinessGate().assess(
        baseline=_baseline(), calibrations=[_calibration()], normalized_cash_flow=_fcf(), run_id="r",
    )
    assert "MISSING_MARKET_DATA" in result.blockers
    assert result.assessment_id.startswith("4e1-")


def test_market_snapshot_is_content_addressed_and_pit() -> None:
    snapshot = MarketSnapshotPIT.from_content(
        ticker="MGLU3", as_of_timestamp=AS_OF, available_at=AS_OF,
        price=10, share_count=100, share_count_basis="SHARES_OUTSTANDING",
        currency="BRL", source_id="b3-close", market_data_version="v1",
        security_type="COMMON_SHARE", equity_value_basis="PRICE_X_SHARES",
    )
    same = MarketSnapshotPIT.from_content(**snapshot.model_dump(exclude={"market_snapshot_id"}))
    assert snapshot.market_snapshot_id == same.market_snapshot_id
    assert snapshot.price > 0 and snapshot.share_count > 0


def test_nonpositive_denominators_are_not_meaningful() -> None:
    baseline = _baseline().model_copy(update={"ttm_net_income": -10, "ttm_ebitda": 0, "ttm_fcf": -2})
    snapshot = MarketSnapshotPIT.from_content(
        ticker="MGLU3", as_of_timestamp=AS_OF, available_at=AS_OF,
        price=10, share_count=100, share_count_basis="SHARES_OUTSTANDING",
        currency="BRL", source_id="b3-close", market_data_version="v1",
        security_type="COMMON_SHARE", equity_value_basis="PRICE_X_SHARES",
    )
    result = ValuationReadinessGate().assess(
        baseline=baseline, calibrations=[], normalized_cash_flow=_fcf(),
        market_snapshot=snapshot,
    )
    assert result.descriptive_metrics["pe_observed"].classification == "NOT_MEANINGFUL_NONPOSITIVE_DENOMINATOR"
    assert result.descriptive_metrics["ev_ebitda_observed"].value is None


def test_klbn_unit_basis_cannot_use_class_aggregate() -> None:
    import pytest
    with pytest.raises(ValueError):
        MarketSnapshotPIT.from_content(
            ticker="KLBN11", as_of_timestamp=AS_OF, available_at=AS_OF,
            price=20, share_count=100, share_count_basis="AGGREGATE_CLASSES",
            currency="BRL", source_id="b3-close", market_data_version="v1",
            security_type="UNIT", equity_value_basis="UNIT_PRICE_X_UNITS",
        )


def test_market_snapshot_ticker_mismatch_is_a_primary_blocker() -> None:
    snapshot = MarketSnapshotPIT.from_content(
        ticker="MGLU3", as_of_timestamp=AS_OF, available_at=AS_OF,
        price=10, share_count=100, share_count_basis="SHARES_OUTSTANDING",
        currency="BRL", source_id="b3-close", market_data_version="v1",
        security_type="COMMON_SHARE", equity_value_basis="PRICE_X_SHARES",
    )
    result = ValuationReadinessGate().assess(
        baseline=_baseline().model_copy(update={"ticker": "SUZB3"}),
        calibrations=[], normalized_cash_flow=_fcf(), market_snapshot=snapshot,
    )
    assert result.status == "VALUATION_BLOCKED_MARKET_SECURITY_MISMATCH"
    assert "MARKET_SECURITY_MISMATCH" in result.blockers


def test_quote_can_be_published_after_quote_timestamp() -> None:
    quote = datetime(2026, 7, 22, 18, tzinfo=timezone.utc)
    published = datetime(2026, 7, 22, 18, 5, tzinfo=timezone.utc)
    assessment = datetime(2026, 7, 22, 23, 59, tzinfo=timezone.utc)
    snapshot = MarketSnapshotPIT.from_content(
        ticker="MGLU3", assessment_as_of=assessment, price_as_of=quote,
        price_available_at=published, share_count_as_of=quote,
        share_count_available_at=published, price=10, share_count=100,
        share_count_basis="SHARES_OUTSTANDING", currency="BRL", source_id="b3-close",
        market_data_version="v1", security_type="COMMON_SHARE",
        equity_value_basis="PRICE_X_SHARES",
    )
    assert snapshot.assessment_as_of == assessment
    assert snapshot.price_available_at > snapshot.price_as_of


def test_pit_ingestor_preserves_b3_and_cvm_provenance() -> None:
    snapshot = PITMarketDataIngestor().build_snapshot(
        ticker="MGLU3", assessment_as_of=AS_OF.replace(hour=23, minute=59),
        price_record={
            "trade_date": "2026-07-22T18:00:00+00:00",
            "available_at": "2026-07-22T18:05:00+00:00",
            "close_price": 10, "currency": "BRL",
            "source_file": "B3_20260722.txt", "source_checksum": "sha-b3",
            "layout_version": "B3-2026-01", "record_hash": "row-hash",
        },
        share_record={
            "as_of": "2026-06-30T00:00:00+00:00",
            "available_at": "2026-07-10T00:00:00+00:00",
            "share_count": 100, "document_id": "FRE-1",
            "document_version": "3", "document_checksum": "sha-cvm",
            "section": "capital_structure",
        },
        security_type="COMMON_SHARE", equity_value_basis="PRICE_X_SHARES",
        share_count_basis="SHARES_OUTSTANDING",
    )
    assert snapshot.price_source_checksum == "sha-b3"
    assert snapshot.share_document_checksum == "sha-cvm"
    assert snapshot.price_available_at > snapshot.price_as_of


def test_b3_cotahist_reader_filters_spot_and_parses_implicit_price(tmp_path) -> None:
    line = bytearray(b" " * 250)
    line[0:2] = b"01"
    line[2:10] = b"22072026"
    line[12:24] = b"MGLU3       "
    line[24:27] = b"010"
    line[108:121] = b"0000000000492"
    line[210:217] = b"0000001"
    line[230:242] = b"BRMGLUACNOR2"
    path = tmp_path / "COTAHIST.txt"
    path.write_bytes(b"00COTAHIST20260723\n" + bytes(line) + b"\n")
    rows = B3CotahistReader().read_text(path, ticker="MGLU3")
    assert len(rows) == 1
    assert rows[0]["close_price"] == pytest.approx(4.92)
    assert rows[0]["market_type"] == "010"


def test_cvm_capital_reader_selects_available_outstanding_row(tmp_path) -> None:
    path = tmp_path / "capital.csv"
    path.write_text(
        "CD_CVM;DT_REFER;DT_RECEB;VERSAO;SHARE_CLASS;SHARES_ISSUED;TREASURY_SHARES\n"
        "123;2026-06-30;2026-07-10;2;ON;1000;100\n"
        "123;2026-06-30;2026-08-10;3;ON;1100;100\n",
        encoding="utf-8",
    )
    rows = CVMCapitalCompositionReader().read(
        path, cvm_code="123", assessment_as_of=datetime(2026, 7, 22, tzinfo=timezone.utc)
    )
    assert len(rows) == 1
    assert rows[0]["outstanding_count"] == 900
    assert rows[0]["document_version"] == "2"
