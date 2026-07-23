"""Sprint 4C.1 point-in-time exposure and company-impact tests."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from macro_b3_bot.application.build_company_exposures import CompanyExposureBuilder
from macro_b3_bot.application.evaluate_company_impacts import CompanyImpactEngine
from macro_b3_bot.domain.causal_models import SectorStateSnapshot
from macro_b3_bot.domain.company_exposure_models import (
    CompanyExposureOverride,
    CompanyExposureSnapshot,
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
        INSERT INTO cvm_documents VALUES (?, 'ITR', '1', '00', DATE '2025-09-30', ?, ?, 'x', 'run')
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
        "INSERT INTO company_ticker_map VALUES ('TEST3','1','00','test',1,TRUE,?)",
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
        sector, exposure(), {"revenue": .5, "cost": -.4, "debt": -.6, "demand": .3}, AS_OF
    )
    assert candidate.net_company_impact is not None
    assert candidate.status in {"WATCH", "NO_ACTION"}
    assert "buy" not in candidate.model_dump()
    incomplete = CompanyImpactEngine("impact-run").evaluate(
        sector, exposure(), {"revenue": .5}, AS_OF
    )
    assert incomplete.status == "NO_ACTION"
    assert set(incomplete.missing_exposures) == {"cost", "debt", "demand"}
