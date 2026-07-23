"""Sprint 4C.1 point-in-time exposure and company-impact tests."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from macro_b3_bot.application.build_company_exposures import CompanyExposureBuilder
from macro_b3_bot.application.audit_company_exposures import CompanyExposureAuditor
from macro_b3_bot.application.evaluate_company_impacts import CompanyImpactEngine
from macro_b3_bot.application.extract_company_macro_exposures import (
    CompanyMacroExposureExtractor,
)
from macro_b3_bot.application.review_company_macro_exposures import (
    CompanyMacroExposureReviewer,
)
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


def test_builder_consumes_only_explicitly_human_approved_facts(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "reviewed-exposure.duckdb")
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
    CompanyMacroExposureExtractor(store)
    item = evidence("export_revenue_pct", .4).model_copy(update={
        "normalized_value": .4,
        "scope_entity": "TEST3",
        "scope_type": "COMPANY_CONSOLIDATED",
        "scope_period": "3Q25",
        "denominator_basis": "TOTAL_REVENUE",
        "extraction_match_confidence": .95,
        "semantic_scope_confidence": .95,
        "denominator_confidence": .95,
        "review_confidence": 0,
        "evidence_excerpt": "Foreign revenue represented 40% of total revenue.",
    })
    payload = item.model_dump(mode="json")
    excerpt_hash = hashlib.sha256(item.evidence_excerpt.encode()).hexdigest()
    store.connection.execute(
        """
        INSERT INTO company_macro_exposure_facts (
            fact_id,selection_run_id,ticker,field_name,normalized_value,
            evidence_payload,methodology_version,review_status,source_excerpt_hash,
            created_at
        ) VALUES ('FACT-1','SOURCE-1','TEST3','export_revenue_pct','0.4',?,
                  'test-v1','HUMAN_REVIEW_PENDING',?,?)
        """,
        [json.dumps(payload), excerpt_hash, AS_OF.replace(tzinfo=None)],
    )
    pending, _ = CompanyExposureBuilder(
        store, "pending", source_selection_run_id="SOURCE-1"
    ).build_snapshot("TEST3", "VAREJO", AS_OF)
    assert pending is not None
    assert pending.export_revenue_pct is None

    manifest = CompanyMacroExposureReviewer(store).pending_manifest("SOURCE-1")
    nonhuman = tmp_path / "nonhuman.json"
    nonhuman.write_text(json.dumps({
        **manifest, "reviewed_by": "automated extractor", "reviewer_type": "AGENT",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="confirmed reviewer identity"):
        CompanyMacroExposureReviewer(store).apply_manifest(nonhuman)
    manifest["reviewed_by"] = "local-reviewer"
    manifest["decisions"][0]["decision"] = "APPROVE"
    manifest["decisions"][0]["notes"] = "Scope and denominator checked against source."
    path = tmp_path / "review.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    CompanyMacroExposureReviewer(store).apply_manifest(
        path, confirmed_identity="local-reviewer", confirmed=True
    )
    approved, _ = CompanyExposureBuilder(
        store, "approved", source_selection_run_id="SOURCE-1"
    ).build_snapshot("TEST3", "VAREJO", AS_OF)
    assert approved is not None
    assert approved.export_revenue_pct == .4
    store.close()


def test_review_hash_covers_semantics_and_manifest_application_is_atomic(
    tmp_path: Path,
) -> None:
    store = DatabaseStore(tmp_path / "atomic-review.duckdb")
    CompanyMacroExposureExtractor(store)
    base = evidence("export_revenue_pct", .4).model_copy(update={
        "normalized_value": .4,
        "scope_entity": "TEST3",
        "scope_type": "COMPANY_CONSOLIDATED",
        "scope_period": "FY2025",
        "denominator_basis": "TOTAL_REVENUE",
        "evidence_excerpt": "Exports represented 40% of total revenue.",
    }).model_dump(mode="json")
    for fact_id, field_name in (
        ("FACT-A", "export_revenue_pct"),
        ("FACT-B", "revenue_foreign_currency_pct"),
    ):
        payload = {**base, "field_name": field_name}
        store.connection.execute(
            """
            INSERT INTO company_macro_exposure_facts (
                fact_id,selection_run_id,ticker,field_name,normalized_value,
                evidence_payload,methodology_version,review_status,is_active,
                created_at
            ) VALUES (?, 'SOURCE-ATOMIC','TEST3',?,'0.4',?,'test-v1',
                      'HUMAN_REVIEW_PENDING',TRUE,?)
            """,
            [fact_id, field_name, json.dumps(payload), AS_OF.replace(tzinfo=None)],
        )
    reviewer = CompanyMacroExposureReviewer(store)
    manifest = reviewer.pending_manifest("SOURCE-ATOMIC")
    for decision in manifest["decisions"]:
        decision["decision"] = "APPROVE"
        decision["notes"] = "Source, scope and denominator independently checked."
    manifest["reviewed_by"] = "local-reviewer"
    manifest["decisions"][-1]["fact_review_hash"] = "invalid"
    path = tmp_path / "atomic.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="content changed"):
        reviewer.apply_manifest(
            path, confirmed_identity="local-reviewer", confirmed=True
        )
    assert store.connection.execute(
        """
        SELECT COUNT(*) FROM company_macro_exposure_facts
        WHERE review_status='HUMAN_APPROVED'
        """
    ).fetchone()[0] == 0
    assert store.connection.execute(
        "SELECT COUNT(*) FROM company_exposure_review_log"
    ).fetchone()[0] == 0

    delegated = reviewer.pending_manifest("SOURCE-ATOMIC")
    delegated["reviewer_type"] = "DELEGATED_AI"
    delegated["reviewed_by"] = "Codex delegated review"
    for decision in delegated["decisions"]:
        decision["decision"] = "APPROVE"
        decision["notes"] = "Evidence, value, scope and denominator were reviewed."
    delegated_path = tmp_path / "delegated.json"
    delegated_path.write_text(json.dumps(delegated), encoding="utf-8")
    result = reviewer.apply_manifest(
        delegated_path,
        confirmed_identity="Codex delegated review",
        confirmed=True,
    )
    assert result["approved"] == 2
    assert store.connection.execute(
        """
        SELECT COUNT(*) FROM company_macro_exposure_facts
        WHERE review_status='DELEGATED_AI_APPROVED'
        """
    ).fetchone()[0] == 2
    assert store.connection.execute(
        """
        SELECT COUNT(*) FROM company_exposure_review_log
        WHERE reviewer_type='DELEGATED_AI'
        """
    ).fetchone()[0] == 2
    delegated_confidence = store.connection.execute(
        """
        SELECT CAST(json_extract(evidence_payload,'$.review_confidence') AS DOUBLE),
               json_extract_string(evidence_payload,'$.review_assurance')
          FROM company_macro_exposure_facts
         WHERE review_status='DELEGATED_AI_APPROVED'
         LIMIT 1
        """
    ).fetchone()
    assert delegated_confidence == (0.75, "DELEGATED_AI")

    original = reviewer.fact_review_hash(
        "FACT-A", "TEST3", "export_revenue_pct", "0.4", base, "test-v1"
    )
    changed = reviewer.fact_review_hash(
        "FACT-A", "TEST3", "export_revenue_pct", "0.4",
        {**base, "scope_type": "ASSET_ONLY"}, "test-v1",
    )
    assert original != changed
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
    proposed = CompanyImpactEngine("impact-run-proposed").evaluate(
        sector, exposure(), {("FX", "revenue"): .5}, AS_OF,
        decision_policy="MATERIALITY_COVERAGE",
        materiality_threshold=.01,
        confidence_threshold=.05,
    )
    assert proposed.status == "WATCH"
    assert proposed.known_component_count == 1
    assert proposed.coverage_penalty == .25


def test_fx_swap_uses_net_debt_and_migrates_to_post_hedge_rates() -> None:
    sector = SectorStateSnapshot(
        snapshot_id="SEC-HEDGE", sector="VAREJO", as_of_timestamp=AS_OF,
        net_impact=-.4, bullish_impact=0, bearish_impact=.4, conflict_ratio=0,
        supporting_event_ids=[], opposing_event_ids=["fx", "rates"],
        confidence=.9, status="SECTOR_STATE_ACTIVE", run_id="sector-run",
        graph_version="1.1.0",
    )
    fx_evidence = evidence("net_foreign_currency_debt_pct", 0.0)
    rate_evidence = evidence("post_hedge_floating_rate_debt_pct", .2)
    hedged = exposure().model_copy(update={
        "contractual_foreign_currency_debt_pct": .2,
        "currency_hedge_pct": 1.0,
        "net_foreign_currency_debt_pct": 0.0,
        "foreign_currency_debt_pct": 0.0,
        "post_hedge_floating_rate_debt_pct": .2,
        "floating_rate_debt_pct": .2,
        "field_evidence": [
            *exposure().field_evidence,
            fx_evidence,
            rate_evidence,
        ],
    })
    candidate = CompanyImpactEngine("hedge-run").evaluate(
        sector, hedged,
        {("FX", "debt"): -.8, ("INTEREST_RATES", "debt"): -.8},
        AS_OF,
    )
    contributions = {
        item.factor: item for item in candidate.factor_contributions
    }
    assert contributions["FX"].exposure_field == "net_foreign_currency_debt_pct"
    assert contributions["FX"].final_contribution == 0
    assert (
        contributions["INTEREST_RATES"].exposure_field
        == "post_hedge_floating_rate_debt_pct"
    )
    assert contributions["INTEREST_RATES"].final_contribution < 0


def test_builder_derives_post_hedge_economic_debt_exposure(tmp_path: Path) -> None:
    store = DatabaseStore(tmp_path / "post-hedge-builder.duckdb")
    store.connection.execute(
        """
        INSERT INTO company_ticker_map (
            ticker,cvm_code,cnpj,mapping_source,confidence,validated,created_at,
            legal_name,valid_from,valid_to,review_status,evidence_id,mapping_version
        ) VALUES ('HEDGE3','1','00','test',1,TRUE,?,'Hedge SA',
                  DATE '2025-01-01',NULL,'VALIDATED','registry','v1')
        """,
        [(AS_OF - timedelta(days=300)).replace(tzinfo=None)],
    )
    insert_document(store, "ITR-HEDGE", AS_OF - timedelta(days=30), 1, 1000)
    CompanyMacroExposureExtractor(store)
    facts = (
        ("contractual_foreign_currency_debt_pct", .2),
        ("currency_hedge_pct", 1.0),
        ("floating_rate_debt_pct", 0.0),
    )
    for index, (field_name, value) in enumerate(facts):
        item = evidence(field_name, value).model_copy(update={
            "normalized_value": value,
            "scope_entity": "HEDGE3",
            "scope_type": "CONTRACTUAL_CURRENCY_BEFORE_HEDGE",
            "denominator_basis": "CONSOLIDATED_GROSS_DEBT",
            "evidence_excerpt": f"Explicit {field_name} disclosure.",
            "rate_exposure_basis": (
                "EXCLUDES_HEDGED_DEBT"
                if field_name == "floating_rate_debt_pct" else None
            ),
        })
        store.connection.execute(
            """
            INSERT INTO company_macro_exposure_facts (
                fact_id,selection_run_id,ticker,field_name,normalized_value,
                evidence_payload,methodology_version,review_status,is_active,
                created_at
            ) VALUES (?, 'SOURCE-HEDGE','HEDGE3',?,?,?,'test-v1',
                      'HUMAN_APPROVED',TRUE,?)
            """,
            [
                f"HEDGE-{index}", field_name, json.dumps(value),
                json.dumps(item.model_dump(mode="json")),
                AS_OF.replace(tzinfo=None),
            ],
        )
    snapshot, reason = CompanyExposureBuilder(
        store, "hedge-build", source_selection_run_id="SOURCE-HEDGE"
    ).build_snapshot("HEDGE3", "VAREJO", AS_OF)
    assert reason is None
    assert snapshot is not None
    assert snapshot.contractual_foreign_currency_debt_pct == .2
    assert snapshot.net_foreign_currency_debt_pct == 0
    assert snapshot.foreign_currency_debt_pct == 0
    assert snapshot.post_hedge_floating_rate_debt_pct == .2
    assert snapshot.floating_rate_debt_pct == .2
    assert {
        item.field_name for item in snapshot.field_evidence
    }.issuperset({
        "net_foreign_currency_debt_pct",
        "post_hedge_floating_rate_debt_pct",
    })
    store.close()


def test_unknown_swap_rate_basis_blocks_post_hedge_rate_derivation() -> None:
    derive = CompanyExposureBuilder._post_hedge_floating_rate
    assert derive(.4, .2, 1.0, "UNKNOWN") is None
    assert derive(.4, .2, 1.0, "INCLUDES_HEDGED_DEBT") == .4
    assert derive(.4, .2, 1.0, "EXCLUDES_HEDGED_DEBT") == .6


def test_no_active_sector_signal_forces_no_action_without_contributions() -> None:
    sector = SectorStateSnapshot(
        snapshot_id="SEC-NULL", sector="VAREJO", as_of_timestamp=AS_OF,
        net_impact=0, bullish_impact=0, bearish_impact=0, conflict_ratio=0,
        supporting_event_ids=[], opposing_event_ids=[], confidence=0,
        status="SECTOR_STATE_NO_ACTIVE_SIGNAL", run_id="sector-run",
        graph_version="1.1.0",
    )
    candidate = CompanyImpactEngine("impact-run").evaluate(
        sector, exposure(), {("FX", "revenue"): .8}, AS_OF
    )
    assert candidate.status == "NO_ACTION"
    assert candidate.reason == "SECTOR_STATE_NO_ACTIVE_SIGNAL"
    assert candidate.factor_contributions == []
    assert candidate.net_company_impact is None


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
    assert hyp.factor_contributions[0].causal_factor_impact == pytest.approx(
        val.factor_contributions[0].causal_factor_impact
    )
    assert hyp.factor_contributions[0].evidence_weight == .4
    assert val.factor_contributions[0].evidence_weight == 1
    assert hyp.factor_contributions[0].adjusted_factor_impact == pytest.approx(
        val.factor_contributions[0].adjusted_factor_impact * .4
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
