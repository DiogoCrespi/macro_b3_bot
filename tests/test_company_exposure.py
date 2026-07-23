"""Sprint 4C.1 point-in-time exposure and company-impact tests."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from macro_b3_bot.application.build_company_exposures import CompanyExposureBuilder
from macro_b3_bot.application.audit_company_exposures import CompanyExposureAuditor
from macro_b3_bot.application.evaluate_company_impacts import CompanyImpactEngine
from macro_b3_bot.application.transport_company_channels import CompanyChannelTransport
from macro_b3_bot.domain.causal_models import SectorStateSnapshot
from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureOverride,
    CompanyExposureSnapshot,
    CompanyFactorChannel,
    ExposureFieldEvidence,
    ExtractionMethod,
)
from macro_b3_bot.infrastructure.store import DatabaseStore

AS_OF = datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc)


def evidence(field: str, value) -> ExposureFieldEvidence:
    return ExposureFieldEvidence(
        field_name=field, value=value, source_type="CVM_DFP", evidence_id="DFP-1",
        available_at=AS_OF - timedelta(days=30),
        extraction_method=ExtractionMethod.EXPLICIT_DISCLOSURE,
        methodology_version="test-v1", confidence=.95, is_estimated=False,
    )


def exposure(**changes) -> CompanyExposureSnapshot:
    values = {
        "exposure_id": "EXP-1", "ticker": "TEST3", "cvm_code": "1",
        "sector": "VAREJO", "as_of_timestamp": AS_OF,
        "reference_date": date(2025, 9, 30), "exposure_version": "test-v1",
        "export_revenue_pct": .4, "cost_foreign_currency_pct": .3,
        "floating_rate_debt_pct": .6, "demand_cyclicality": .8,
        "field_evidence": [
            evidence("export_revenue_pct", .4), evidence("cost_foreign_currency_pct", .3),
            evidence("floating_rate_debt_pct", .6), evidence("demand_cyclicality", .8),
        ],
        "missing_fields": [], "confidence": .9, "run_id": "run",
        "created_at": AS_OF,
    }
    values.update(changes)
    return CompanyExposureSnapshot(**values)


def insert_document(
    store: DatabaseStore, document_id: str, received_at: datetime, version: int,
    revenue: float,
) -> None:
    store.connection.execute(
        """
        INSERT INTO cvm_documents (
            document_id,document_type,cvm_code,cnpj,reference_date,received_at,
            version,raw_zip_checksum,ingestion_run_id,availability_basis,source_url
        ) VALUES (?, 'ITR', '1', '00', DATE '2025-09-30', ?, ?, 'x', 'run',
                  'TEST_FIXTURE','fixture://itr')
        """,
        [document_id, received_at.replace(tzinfo=None), version],
    )
    for account, value in (("3.01", revenue), ("2.01.04", 100.0), ("2.02.01", 200.0)):
        store.connection.execute(
            """
            INSERT INTO financial_statement_lines VALUES (
                ?, 'DRE', 'CONSOLIDATED', 'LAST', ?, 'test', ?, 'BRL', 1,
                DATE '2025-01-01', DATE '2025-09-30', ?
            )
            """,
            [document_id, account, value, f"{document_id}-{account}"],
        )


def test_optional_fields_remain_unknown_not_zero() -> None:
    item = exposure(export_revenue_pct=None, field_evidence=[
        evidence("cost_foreign_currency_pct", .3),
        evidence("floating_rate_debt_pct", .6),
        evidence("demand_cyclicality", .8),
    ])
    assert item.export_revenue_pct is None


def test_value_requires_field_level_evidence() -> None:
    with pytest.raises(ValueError, match="without field-level evidence"):
        exposure(pricing_power=.8)


def test_geography_and_commodity_have_distinct_semantics() -> None:
    with pytest.raises(ValueError, match="geographic"):
        exposure(
            geographic_exposures={"BRAZIL": .8, "EXPORT": .4},
            field_evidence=exposure().field_evidence + [evidence("geographic_exposures", {"BRAZIL": .8, "EXPORT": .4})],
        )
    valid = exposure(
        commodity_exposures={"OIL": -.4},
        field_evidence=exposure().field_evidence + [evidence("commodity_exposures", {"OIL": -.4})],
    )
    assert valid.commodity_exposures == {"OIL": -.4}


def test_builder_selects_document_point_in_time_and_excludes_future_override(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "pit-exposure.duckdb")
    store.connection.execute(
        """
        INSERT INTO company_ticker_map (
            ticker,cvm_code,cnpj,mapping_source,confidence,validated,created_at,
            legal_name,valid_from,valid_to,review_status,evidence_id,mapping_version
        ) VALUES ('TEST3','1','00','test',1,TRUE,?,'Test SA',DATE '2025-01-01',
                  NULL,'VALIDATED','registry-test','v1')
        """,
        [(AS_OF - timedelta(days=300)).replace(tzinfo=None)],
    )
    insert_document(store, "ITR-v1", AS_OF - timedelta(days=30), 1, 1000)
    insert_document(store, "ITR-v2", AS_OF + timedelta(days=30), 2, 9999)
    override = CompanyExposureOverride(
        override_id="future", ticker="TEST3", field_name="export_revenue_pct",
        new_value=.9, rationale="Future reviewed disclosure", evidence_ids=["IPE-future"],
        approved_by="reviewer", approved_at=AS_OF + timedelta(days=1),
        methodology_version="test", run_id="override-run",
    )
    store.save_company_exposure_override(override.model_dump(mode="json"))
    snapshot, reason = CompanyExposureBuilder(store, "build-run").build_snapshot(
        "TEST3", "VAREJO", AS_OF
    )
    assert reason is None
    assert snapshot is not None
    assert snapshot.total_revenue == 1000
    assert snapshot.total_debt == 300
    assert snapshot.export_revenue_pct is None
    assert {item.evidence_id for item in snapshot.field_evidence} == {"ITR-v1"}
    store.save_company_exposure_snapshot(snapshot.model_dump(mode="json"))
    audit = CompanyExposureAuditor(store).audit_run("build-run")
    assert len(audit) == 2
    assert {row["validation_status"] for row in audit} == {"VALIDATED"}
    assert {row["absolute_difference"] for row in audit} == {0}
    store.close()


def test_builder_does_not_treat_bank_deposits_as_corporate_debt(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "bank-exposure.duckdb")
    store.connection.execute(
        """
        INSERT INTO company_ticker_map (
            ticker,cvm_code,cnpj,mapping_source,confidence,validated,created_at,
            legal_name,valid_from,valid_to,review_status,evidence_id,mapping_version
        ) VALUES ('TEST3','1','00','test',1,TRUE,?,'Test Bank',DATE '2025-01-01',
                  NULL,'VALIDATED','registry-test','v1')
        """,
        [(AS_OF - timedelta(days=300)).replace(tzinfo=None)],
    )
    insert_document(store, "ITR-BANK", AS_OF - timedelta(days=30), 1, 1000)
    builder = CompanyExposureBuilder(store, "RUN_BANK")

    snapshot, reason = builder.build_snapshot("TEST3", "BANCOS", AS_OF)

    assert reason is None
    assert snapshot is not None
    assert snapshot.total_debt is None
    assert "total_debt" in snapshot.missing_fields
    assert all(item.field_name != "total_debt" for item in snapshot.field_evidence)
    store.close()


def test_builder_reports_missing_pilot_sources_without_fabrication(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "missing.duckdb")
    summary = CompanyExposureBuilder(store, "run").build_pilot(
        AS_OF, [{"ticker": "NONE3", "sector": "VAREJO"}]
    )
    assert summary["snapshots_built"] == 0
    assert summary["missing_mapping"] == ["NONE3"]
    store.close()


def test_company_impact_requires_explicit_factor_context_and_never_buys() -> None:
    sector = SectorStateSnapshot(
        snapshot_id="SEC-1", sector="VAREJO", as_of_timestamp=AS_OF,
        net_impact=.4, bullish_impact=.6, bearish_impact=.2, conflict_ratio=.33,
        supporting_event_ids=["growth"], opposing_event_ids=["rates"],
        confidence=.8, status="SECTOR_STATE_ACTIVE", run_id="sector-run",
        graph_version="1.1.0",
    )
    candidate = CompanyImpactEngine("impact-run").evaluate(
        sector, exposure(), {
            ("FX", "revenue"): .5, ("FX", "cost"): -.4,
            ("INTEREST_RATES", "debt"): -.6,
            ("ECONOMIC_ACTIVITY", "demand"): .3,
        }, AS_OF
    )
    assert candidate.net_company_impact is not None
    assert candidate.status in {"WATCH", "NO_ACTION"}
    assert "buy" not in candidate.model_dump()
    incomplete = CompanyImpactEngine("impact-run").evaluate(
        sector, exposure(), {("FX", "revenue"): .5}, AS_OF
    )
    assert incomplete.status == "NO_ACTION"
    assert set(incomplete.missing_exposures) == {"cost", "debt", "demand"}


def test_channel_transport_preserves_fx_channels_and_opposite_direction() -> None:
    from macro_b3_bot.domain.causal_models import SectorImpactCandidate

    base = {
        "candidate_id": "C1", "event_id": "E1", "event_type": "USD_BRL_SHOCK",
        "causal_root": "USD_BRL_SHOCK_UP", "sector": "PAPEL_CELULOSE",
        "direction": "BULLISH", "impact_score": .6, "event_strength": .8,
        "confidence": .7, "causal_paths": [{
            "path_id": "PATH-FX",
            "nodes": ["USD_BRL_SHOCK_UP", "USD_BRL_UP",
                      "B3_SECTOR_PAPEL_CELULOSE"],
            "causal_edge_ids": ["edge-usd", "edge-pulp"],
            "factor": "FX",
            "company_channel_effects": {"revenue": 1, "cost": -1, "debt": -1},
            "factor_direction": 1, "direction": 1, "strength": .6,
            "confidence": .7, "evidence_ids": [],
            "evidence_status": "HYPOTHESIS",
        }],
        "evidence_status": "HYPOTHESIS", "detected_at": AS_OF,
        "event_available_at": AS_OF, "as_of_timestamp": AS_OF, "run_id": "sector",
        "source_event_run_id": "macro", "graph_version": "1.1.0",
    }
    channels = CompanyChannelTransport().from_sector_candidates([
        SectorImpactCandidate(**base)
    ])
    directions = {item.channel: item.direction for item in channels}
    assert directions == {"cost": -1, "debt": -1, "revenue": 1}

    candidate = CompanyImpactEngine("impact-run").evaluate(
        SectorStateSnapshot(
            snapshot_id="SEC-FX", sector="PAPEL_CELULOSE", as_of_timestamp=AS_OF,
            net_impact=.4, bullish_impact=.4, bearish_impact=0, conflict_ratio=0,
            supporting_event_ids=["E1"], confidence=.7, status="ACTIVE",
            run_id="sector", graph_version="1.1.0",
        ),
        exposure(
            sector="PAPEL_CELULOSE",
            revenue_foreign_currency_pct=.7,
            foreign_currency_debt_pct=.2,
            field_evidence=exposure().field_evidence + [
                evidence("revenue_foreign_currency_pct", .7),
                evidence("foreign_currency_debt_pct", .2),
            ],
        ),
        None,
        AS_OF,
        factor_channels=channels,
    )
    assert candidate.revenue_impact_score is not None
    assert candidate.debt_impact_score is not None
    assert candidate.cost_impact_score is not None
    assert candidate.revenue_impact_score > 0
    assert candidate.debt_impact_score < 0


def test_factor_channel_requires_traceable_evidence() -> None:
    with pytest.raises(ValueError):
        CompanyFactorChannel(
            factor="FX", channel="revenue", direction=1, strength=.5,
            confidence=.5, source_path_ids=[], causal_edge_ids=[],
            evidence_ids=[], evidence_status="HYPOTHESIS",
        )
    hypothesis = CompanyFactorChannel(
        factor="FX", channel="revenue", direction=1, strength=.5,
        confidence=.5, source_path_ids=["PATH-1"], causal_edge_ids=["EDGE-1"],
        evidence_ids=[], evidence_status="HYPOTHESIS",
    )
    assert hypothesis.evidence_ids == []


@pytest.mark.parametrize(
    ("factor", "channel", "field_name", "value", "expected_sign"),
    [
        ("FX", "debt", "foreign_currency_debt_pct", .4, -1),
        ("INTEREST_RATES", "debt", "floating_rate_debt_pct", .4, -1),
        ("INFLATION", "debt", "inflation_linked_debt_pct", .4, -1),
        ("ECONOMIC_ACTIVITY", "demand", "demand_cyclicality", .4, 1),
    ],
)
def test_factor_specific_matrix_uses_only_relevant_field(
    factor: str, channel: str, field_name: str, value: float, expected_sign: int
) -> None:
    item = exposure(
        **{field_name: value},
        field_evidence=exposure().field_evidence + [evidence(field_name, value)],
    )
    candidate = CompanyImpactEngine("factor-matrix").evaluate(
        _sector("VAREJO"), item,
        {(factor, channel): float(expected_sign)}, AS_OF,
    )
    result = getattr(candidate, f"{channel}_impact_score")
    assert result is not None
    assert result * expected_sign > 0


@pytest.mark.parametrize(("sensitivity", "channel", "factor_impact", "sign"), [
    (.8, "revenue", 1.0, 1),
    (.8, "revenue", -1.0, -1),
    (-.8, "cost", -1.0, -1),
    (-.8, "cost", 1.0, 1),
])
def test_oil_uses_signed_commodity_exposure(
    sensitivity: float, channel: str, factor_impact: float, sign: int
) -> None:
    item = exposure(
        commodity_exposures={"OIL": sensitivity},
        field_evidence=exposure().field_evidence + [
            evidence("commodity_exposures", {"OIL": sensitivity})
        ],
    )
    candidate = CompanyImpactEngine("oil-matrix").evaluate(
        _sector("VAREJO"), item, {("OIL", channel): factor_impact}, AS_OF,
    )
    result = getattr(candidate, f"{channel}_impact_score")
    assert result is not None
    assert result * sign > 0


def test_irrelevant_factor_does_not_use_wrong_debt_field() -> None:
    candidate = CompanyImpactEngine("irrelevant").evaluate(
        _sector("VAREJO"), exposure(),
        {("FX", "debt"): -1.0}, AS_OF,
    )
    assert candidate.debt_impact_score is None
    assert candidate.status == "NO_ACTION"


def test_pricing_power_does_not_modify_fx_revenue() -> None:
    base_evidence = exposure().field_evidence
    without = exposure()
    with_pricing = exposure(
        pricing_power=0,
        field_evidence=base_evidence + [evidence("pricing_power", 0)],
    )
    impacts = {("FX", "revenue"): 1.0}
    first = CompanyImpactEngine("modifier").evaluate(
        _sector("VAREJO"), without, impacts, AS_OF
    )
    second = CompanyImpactEngine("modifier").evaluate(
        _sector("VAREJO"), with_pricing, impacts, AS_OF
    )
    assert first.revenue_impact_score == second.revenue_impact_score


def test_operating_leverage_has_neutral_midpoint_and_symmetric_beta() -> None:
    def item(value: float) -> CompanyExposureSnapshot:
        return exposure(
            commodity_exposures={"OIL": -.8}, operating_leverage=value,
            field_evidence=exposure().field_evidence + [
                evidence("commodity_exposures", {"OIL": -.8}),
                evidence("operating_leverage", value),
            ],
        )

    scores = [
        CompanyImpactEngine("modifier").evaluate(
            _sector("VAREJO"), item(value), {("OIL", "cost"): -1.0}, AS_OF
        ).factor_contributions[0].final_contribution
        for value in (0, .5, 1)
    ]
    assert abs(scores[0]) < abs(scores[1]) < abs(scores[2])


def test_factor_contribution_keeps_trace_and_hypothesis_weight() -> None:
    common = {
        "factor": "FX", "channel": "debt", "direction": -1,
        "strength": 1, "confidence": 1, "source_path_ids": ["PATH-1"],
        "causal_edge_ids": ["EDGE-1"],
    }
    hypothesis = CompanyFactorChannel(
        **common, evidence_ids=[], evidence_status="HYPOTHESIS"
    )
    validated = CompanyFactorChannel(
        **common, evidence_ids=["PAPER-1"], evidence_status="VALIDATED"
    )
    item = exposure(
        foreign_currency_debt_pct=.5,
        field_evidence=exposure().field_evidence + [
            evidence("foreign_currency_debt_pct", .5)
        ],
    )
    hyp = CompanyImpactEngine("hyp").evaluate(
        _sector("VAREJO"), item, None, AS_OF, [hypothesis]
    )
    val = CompanyImpactEngine("val").evaluate(
        _sector("VAREJO"), item, None, AS_OF, [validated]
    )
    assert hyp.causal_evidence_status == "HYPOTHESIS"
    assert val.causal_evidence_status == "VALIDATED"
    assert hyp.factor_contributions[0].raw_factor_impact == pytest.approx(
        val.factor_contributions[0].raw_factor_impact * .4
    )
    assert hyp.source_path_ids == ["PATH-1"]
    assert hyp.causal_edge_ids == ["EDGE-1"]
    assert hyp.supporting_event_ids == ["event"]


def test_unsupported_factor_channel_is_explicit() -> None:
    candidate = CompanyImpactEngine("unsupported").evaluate(
        _sector("VAREJO"), exposure(),
        {("COUNTRY_RISK", "debt"): -1.0}, AS_OF,
    )
    assert candidate.unsupported_factor_channels[0].model_dump() == {
        "factor": "COUNTRY_RISK", "channel": "debt",
        "reason": "NO_EXPOSURE_MAPPING", "expected_fields": [],
    }


def test_factor_contributions_are_persisted(tmp_path: Path) -> None:
    item = exposure(
        foreign_currency_debt_pct=.5,
        field_evidence=exposure().field_evidence + [
            evidence("foreign_currency_debt_pct", .5)
        ],
    )
    candidate = CompanyImpactEngine("persist").evaluate(
        _sector("VAREJO"), item, {("FX", "debt"): -1.0}, AS_OF
    )
    store = DatabaseStore(tmp_path / "impact-audit.duckdb")
    assert store.save_company_impact_candidate(candidate.model_dump(mode="json"))
    payload = store.connection.execute(
        "SELECT impact_payload FROM company_impact_candidates"
    ).fetchone()[0]
    assert '"factor_contributions"' in payload
    assert '"missing_factor_exposures"' in payload
    assert '"causal_evidence_status"' in payload
    store.close()


def _sector(sector: str) -> SectorStateSnapshot:
    return SectorStateSnapshot(
        snapshot_id=f"SEC-{sector}", sector=sector, as_of_timestamp=AS_OF,
        net_impact=.4, bullish_impact=.4, bearish_impact=0, conflict_ratio=0,
        supporting_event_ids=["event"], confidence=.8, status="ACTIVE",
        run_id="sector", graph_version="1.2.0",
    )
