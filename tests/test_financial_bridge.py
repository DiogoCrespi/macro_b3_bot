"""Sprint 4D.1 baseline and shock-to-earnings bridge tests."""
from datetime import date, datetime, timedelta, timezone

import pytest

from macro_b3_bot.application.build_financial_baselines import (
    FinancialBaselineBuilder,
)
from macro_b3_bot.application.evaluate_financial_scenarios import (
    FinancialScenarioEngine,
)
from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureSnapshot,
    CompanyImpactCandidate,
    ExposureFieldEvidence,
    ExtractionMethod,
    FactorContribution,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


AS_OF = datetime(2026, 7, 22, 23, 59, 59, tzinfo=timezone.utc)


def _evidence(field: str, value: float) -> ExposureFieldEvidence:
    return ExposureFieldEvidence(
        field_name=field, value=value, normalized_value=value,
        source_type="CVM_ITR", evidence_id=f"E-{field}",
        available_at=AS_OF - timedelta(days=30),
        extraction_method=ExtractionMethod.EXPLICIT_DISCLOSURE,
        methodology_version="test", confidence=.9,
        review_confidence=.75, review_assurance="DELEGATED_AI",
    )


def _exposure() -> CompanyExposureSnapshot:
    fields = {
        "revenue_foreign_currency_pct": .4,
        "net_foreign_currency_debt_pct": .1,
        "post_hedge_floating_rate_debt_pct": .5,
        "inflation_linked_debt_pct": .2,
    }
    return CompanyExposureSnapshot(
        exposure_id="EXP", ticker="TEST3", cvm_code="1", sector="VAREJO",
        as_of_timestamp=AS_OF, reference_date=date(2026, 3, 31),
        exposure_version="test", **fields,
        field_evidence=[
            _evidence(field, value) for field, value in fields.items()
        ],
        missing_fields=[], confidence=.9, evidence_quality_score=.9,
        completeness_score=.2, run_id="exposure", created_at=AS_OF,
    )


def _insert_document(
    store: DatabaseStore,
    document_id: str,
    document_type: str,
    reference_date: str,
) -> None:
    store.connection.execute(
        """
        INSERT INTO cvm_documents (
            document_id,document_type,cvm_code,cnpj,reference_date,received_at,
            version,raw_zip_checksum,ingestion_run_id,availability_basis,
            filing_available_at,availability_precision
        ) VALUES (?,?, '1','00',?,TIMESTAMP '2026-04-30',1,?,'run',
                  'FILING_AVAILABLE_AT',TIMESTAMP '2026-04-30','EXACT')
        """,
        [document_id, document_type, reference_date, document_id],
    )


def _line(
    store: DatabaseStore,
    document: str,
    statement: str,
    order: str,
    account: str,
    value: float,
) -> None:
    store.connection.execute(
        """
        INSERT INTO financial_statement_lines VALUES (
            ?,?,'CONSOLIDATED',?,?,'test',?,'BRL',1,
            DATE '2025-01-01',
            CASE WHEN ? LIKE 'ITR%' THEN DATE '2026-03-31'
                 ELSE DATE '2025-12-31' END,
            ?
        )
        """,
        [
            document, statement, order, account, value, document,
            f"{document}-{statement}-{order}-{account}",
        ],
    )


def _baseline_store(tmp_path) -> DatabaseStore:
    store = DatabaseStore(tmp_path / "financial.duckdb")
    store.connection.execute(
        """
        INSERT INTO company_ticker_map (
            ticker,cvm_code,cnpj,mapping_source,confidence,validated,created_at,
            legal_name,valid_from,review_status,evidence_id,mapping_version
        ) VALUES ('TEST3','1','00','test',1,TRUE,TIMESTAMP '2025-01-01',
                  'Test SA',DATE '2025-01-01','VALIDATED','map','v1')
        """
    )
    _insert_document(store, "DFP-1", "DFP", "2025-12-31")
    _insert_document(store, "ITR-1", "ITR", "2026-03-31")
    flow = {
        ("DRE", "3.01"): (1000, 300, 200),
        ("DRE", "3.02"): (-600, -180, -120),
        ("DRE", "3.05"): (200, 60, 40),
        ("DRE", "3.06"): (-30, -10, -5),
        ("DRE", "3.07"): (170, 50, 35),
        ("DRE", "3.08"): (-50, -15, -10),
        ("DRE", "3.11"): (120, 35, 25),
        ("DFC-MI", "6.01"): (150, 40, 30),
        ("DFC-MI", "6.02.01"): (-50, -15, -10),
        ("DFC-MI", "6.01.01.02"): (50, 15, 10),
    }
    for (statement, account), (annual, current, prior) in flow.items():
        _line(store, "DFP-1", statement, "ÚLTIMO", account, annual)
        _line(store, "ITR-1", statement, "ÚLTIMO", account, current)
        _line(store, "ITR-1", statement, "PENÚLTIMO", account, prior)
    for statement, account, fy, current in (
        ("BPA", "1.01.01", 80, 100),
        ("BPA", "1.01", 450, 500),
        ("BPP", "2.01", 280, 300),
        ("BPP", "2.01.04", 100, 120),
        ("BPP", "2.02.01", 260, 280),
    ):
        _line(store, "DFP-1", statement, "ÚLTIMO", account, fy)
        _line(store, "ITR-1", statement, "ÚLTIMO", account, current)
    return store


def _factor(
    factor: str, channel: str, field: str, value: float, adjusted: float = .5
) -> FactorContribution:
    return FactorContribution(
        factor=factor, channel=channel, causal_factor_impact=.8,
        evidence_weight=.4, adjusted_factor_impact=adjusted,
        exposure_field=field, exposure_value=value, exposure_confidence=.9,
        exposure_evidence_ids=[f"E-{field}"], final_contribution=.1,
        source_path_ids=["PATH"], causal_edge_ids=["EDGE"],
        evidence_ids=[], evidence_status="HYPOTHESIS",
    )


def _candidate(contributions, reason="MATERIAL_APPROVED_CONTRIBUTION"):
    return CompanyImpactCandidate(
        candidate_id="CAND", ticker="TEST3", sector_snapshot_id="SEC",
        company_exposure_id="EXP", as_of_timestamp=AS_OF,
        revenue_impact_score=.1, cost_impact_score=None,
        debt_impact_score=-.1, demand_impact_score=None,
        net_company_impact=0, confidence=.2, conflict_ratio=0,
        factor_contributions=contributions,
        causal_evidence_status="HYPOTHESIS",
        missing_exposures=["cost", "demand"], status="WATCH", reason=reason,
        decision_policy="MATERIALITY_COVERAGE", known_component_count=2,
        coverage_penalty=.5, run_id="impact",
    )


def test_ttm_baseline_is_pit_evidenced_and_uses_monetary_denominators(tmp_path) -> None:
    store = _baseline_store(tmp_path)
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, _exposure()
    )
    assert baseline.ttm_revenue == 1100
    assert baseline.ttm_costs == -660
    assert baseline.ttm_ebit == 220
    assert baseline.ttm_ebitda == 275
    assert baseline.ttm_financial_result == -35
    assert baseline.ttm_net_income == 130
    assert baseline.ttm_operating_cash_flow == 160
    assert baseline.ttm_capex == -55
    assert baseline.ttm_fcf == 105
    assert baseline.fcf_definition == "CFO_PLUS_REPORTED_CAPEX"
    assert baseline.fcf_normalization_status == "NOT_NORMALIZED"
    assert baseline.average_debt_method == "TWO_POINT_AVERAGE_PROXY"
    assert baseline.net_debt_method == "STANDARDIZED_CASH_ONLY"
    assert baseline.gross_debt == 400
    assert baseline.cash == 100
    assert baseline.net_debt == 300
    assert baseline.average_gross_debt == 380
    assert baseline.average_floating_debt == 190
    assert baseline.average_net_fx_debt == 38
    assert baseline.inflation_linked_debt == 76
    assert baseline.working_capital == 200
    assert all(
        available <= AS_OF
        for item in baseline.field_evidence for available in item.available_at
    )
    assert store.connection.execute(
        "SELECT COUNT(*) FROM financial_baseline_snapshots"
    ).fetchone()[0] == 1
    store.close()


def test_scores_are_relevance_only_and_never_financial_percentages(tmp_path) -> None:
    store = _baseline_store(tmp_path)
    exposure = _exposure()
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, exposure
    )
    low = _candidate([
        _factor(
            "INTEREST_RATES", "debt",
            "post_hedge_floating_rate_debt_pct", .5, .2,
        )
    ])
    high = low.model_copy(update={
        "candidate_id": "HIGH",
        "factor_contributions": [
            _factor(
                "INTEREST_RATES", "debt",
                "post_hedge_floating_rate_debt_pct", .5, .8,
            )
        ],
    })
    engine = FinancialScenarioEngine("bridge")
    low_base = next(item for item in engine.evaluate(
        baseline, exposure, low
    ) if item.case == "BASE")
    high_base = next(item for item in engine.evaluate(
        baseline, exposure, high
    ) if item.case == "BASE")
    assert low_base.absolute_changes.financial_result == pytest.approx(-1.9)
    assert high_base.absolute_changes.financial_result == pytest.approx(-1.9)
    assert low_base.confidence < high_base.confidence
    store.close()


def test_fx_revenue_and_fx_debt_use_separate_monetary_bases(tmp_path) -> None:
    store = _baseline_store(tmp_path)
    exposure = _exposure()
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, exposure
    )
    candidate = _candidate([
        _factor("FX", "revenue", "revenue_foreign_currency_pct", .4),
        _factor("FX", "debt", "net_foreign_currency_debt_pct", .1),
    ])
    base = next(item for item in FinancialScenarioEngine("bridge").evaluate(
        baseline, exposure, candidate
    ) if item.case == "BASE")
    contributions = {item.channel: item for item in base.contributions}
    assert contributions["revenue"].monetary_base_field == "ttm_revenue"
    assert contributions["revenue"].monetary_base_value == 1100
    assert contributions["debt"].monetary_base_field == "average_net_fx_debt"
    assert contributions["debt"].monetary_base_value == 38
    assert contributions["revenue"].delta_revenue == 22
    assert contributions["debt"].delta_financial_result == pytest.approx(-3.8)
    assert contributions["debt"].accounting_fx_revaluation == pytest.approx(-3.8)
    assert contributions["debt"].delta_operating_cash_flow == 0
    assert contributions["debt"].delta_fcf == 0
    assert contributions["debt"].delta_net_debt == 0
    assert (
        contributions["revenue"].assumption_calibration_status
        == "ASSUMPTION_NOT_COMPANY_CALIBRATED"
    )
    store.close()


@pytest.mark.parametrize(
    ("factor", "channel", "field", "positive_attr"),
    [
        ("FX", "revenue", "revenue_foreign_currency_pct", "delta_revenue"),
        ("FX", "debt", "net_foreign_currency_debt_pct", "delta_financial_result"),
        (
            "INTEREST_RATES",
            "debt",
            "post_hedge_floating_rate_debt_pct",
            "delta_financial_result",
        ),
    ],
)
def test_causal_up_and_down_produce_opposite_financial_signs(
    tmp_path, factor, channel, field, positive_attr
) -> None:
    store = _baseline_store(tmp_path)
    exposure = _exposure()
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, exposure
    )
    up = _candidate([_factor(factor, channel, field, .5, adjusted=.5)])
    down = _candidate([_factor(factor, channel, field, .5, adjusted=-.5)])
    engine = FinancialScenarioEngine("bridge")
    up_base = next(
        item for item in engine.evaluate(baseline, exposure, up)
        if item.shock_case == "BASE_SHOCK"
    )
    down_base = next(
        item for item in engine.evaluate(baseline, exposure, down)
        if item.shock_case == "BASE_SHOCK"
    )
    up_value = getattr(up_base.contributions[0], positive_attr)
    down_value = getattr(down_base.contributions[0], positive_attr)
    assert up_value == pytest.approx(-down_value)
    assert up_base.contributions[0].signed_shock_magnitude > 0
    assert down_base.contributions[0].signed_shock_magnitude < 0
    store.close()


def test_result_labels_are_assigned_after_company_financial_effect(tmp_path) -> None:
    store = _baseline_store(tmp_path)
    exposure = _exposure()
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, exposure
    )
    candidate = _candidate([
        _factor("FX", "revenue", "revenue_foreign_currency_pct", .4)
    ])
    outcomes = FinancialScenarioEngine("bridge").evaluate(
        baseline, exposure, candidate
    )
    by_result = {item.case: item for item in outcomes}
    assert (
        by_result["PESSIMISTIC"].metrics.fcf
        <= by_result["BASE"].metrics.fcf
        <= by_result["OPTIMISTIC"].metrics.fcf
    )
    assert by_result["PESSIMISTIC"].shock_case == "LOW_SHOCK"
    assert by_result["OPTIMISTIC"].shock_case == "HIGH_SHOCK"
    store.close()


def test_missing_elasticity_is_blocked_instead_of_invented(tmp_path) -> None:
    store = _baseline_store(tmp_path)
    exposure = _exposure()
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, exposure
    )
    candidate = _candidate([
        _factor("INTEREST_RATES", "demand", "demand_cyclicality", .8)
    ])
    outcomes = FinancialScenarioEngine("bridge").evaluate(
        baseline, exposure, candidate
    )
    assert all(item.status == "BLOCKED" for item in outcomes)
    assert all(
        item.blocked_channels[0].reason
        == "BRIDGE_BLOCKED_MISSING_ELASTICITY"
        for item in outcomes
    )
    store.close()


def test_no_active_signal_produces_no_action_and_zero_deltas(tmp_path) -> None:
    store = _baseline_store(tmp_path)
    exposure = _exposure()
    baseline = FinancialBaselineBuilder(store, "baseline").build(
        "TEST3", AS_OF, exposure
    )
    candidate = _candidate([], reason="SECTOR_STATE_NO_ACTIVE_SIGNAL")
    outcomes = FinancialScenarioEngine("bridge").evaluate(
        baseline, exposure, candidate
    )
    assert all(item.status == "NO_ACTION" for item in outcomes)
    assert all(not item.contributions for item in outcomes)
    assert all(item.absolute_changes.fcf == 0 for item in outcomes)
    store.close()
