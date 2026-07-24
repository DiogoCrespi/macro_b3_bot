"""Sprint 4D.3 conflict, calibration-mode and normalized-FCF tests."""
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from macro_b3_bot.application.calibrate_financial_bridges import (
    FinancialBridgeCalibrator,
)
from macro_b3_bot.application.build_financial_baselines import select_anchor_documents
from macro_b3_bot.application.run_financial_calibration_pilot import (
    FinancialCalibrationPilot,
)
from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    BridgeReplayObservation,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


def _anchor_doc(doc_type: str, ref: date, version: int, available_day: int, cvm: str = "22470") -> dict:
    return {
        "cvm_code": cvm, "document_id": f"{doc_type}-{ref}-{version}",
        "document_type": doc_type, "reference_date": ref, "version": version,
        "available_at": datetime(2026, 1, available_day, tzinfo=timezone.utc),
    }


def test_anchor_precedence_dfp_newer_than_existing_itr() -> None:
    selected, anchor, _ = select_anchor_documents([
        _anchor_doc("ITR", date(2025, 9, 30), 1, 10),
        _anchor_doc("DFP", date(2024, 12, 31), 1, 10),
        _anchor_doc("DFP", date(2025, 12, 31), 1, 20),
    ], "MGLU3")
    assert anchor.anchor_document_type == "DFP"
    assert anchor.ttm_method == "DFP_ANNUAL_DIRECT"
    assert selected["DFP"]["reference_date"] == date(2025, 12, 31)


def test_anchor_precedence_itr_before_dfp_publication() -> None:
    selected, anchor, _ = select_anchor_documents([
        _anchor_doc("ITR", date(2025, 9, 30), 1, 10),
        _anchor_doc("DFP", date(2024, 12, 31), 1, 10),
    ], "MGLU3")
    assert anchor.anchor_document_type == "ITR"
    assert selected["DFP"]["reference_date"] == date(2024, 12, 31)


def test_anchor_precedence_itr_2026_uses_dfp_2025() -> None:
    selected, anchor, _ = select_anchor_documents([
        _anchor_doc("DFP", date(2025, 12, 31), 1, 10),
        _anchor_doc("ITR", date(2026, 3, 31), 1, 20),
    ], "MGLU3")
    assert anchor.anchor_document_type == "ITR"
    assert selected["DFP"]["reference_date"] == date(2025, 12, 31)


def test_anchor_republication_and_cvm_identity() -> None:
    selected, _, cvm = select_anchor_documents([
        _anchor_doc("DFP", date(2025, 12, 31), 9, 10),
        _anchor_doc("DFP", date(2025, 12, 31), 10, 20),
    ], "MGLU3")
    assert selected["DFP"]["version"] == 10
    assert cvm == "22470"
    with pytest.raises(ValueError):
        select_anchor_documents([_anchor_doc("ITR", date(2026, 3, 31), 1, 20)], "MGLU3")


AS_OF = datetime(2026, 7, 22, 23, 59, 59, tzinfo=timezone.utc)


def _insert_sector_path(
    store: DatabaseStore,
    *,
    candidate_id: str,
    event_id: str,
    direction: int,
    horizon: int = 180,
) -> None:
    path = [{
        "path_id": f"path-{candidate_id}",
        "nodes": ["USD_REGIME_SHIFT_UP", "VAR_BRL_USD_RATE", "SECTOR"],
        "causal_edge_ids": ["edge_usd_up_to_fx", "edge_fx_to_pulp_paper"],
        "factor": "FX",
        "company_channel_effects": {"revenue": 1, "debt": -1},
        "factor_direction": direction,
        "direction": direction,
        "strength": .8,
        "confidence": .7,
        "evidence_ids": [],
        "evidence_status": "HYPOTHESIS",
    }]
    store.connection.execute(
        """
        INSERT INTO sector_impact_candidates (
            candidate_id,event_id,event_type,sector,direction,impact_score,
            confidence,horizon_months,causal_paths,direct_effects,
            second_order_effects,positive_paths_count,negative_paths_count,
            conflict_detected,invalidators,status,detected_at,causal_root,
            event_strength,horizon_days,evidence_status,event_available_at,
            as_of_timestamp,run_id,source_event_run_id,graph_version
        ) VALUES (?,?, 'USD_REGIME_SHIFT','PAPEL_CELULOSE','BULLISH',.2,.7,6,
                  ?,'[]','[]',1,0,FALSE,'[]','SECTOR_IMPACT_WATCH',?,
                  'USD_REGIME_SHIFT_UP',.8,?,'HYPOTHESIS',?,?,'sector-test',
                  'macro-test','1.2.0')
        """,
        [
            candidate_id,
            event_id,
            json.dumps(path),
            AS_OF.replace(tzinfo=None),
            horizon,
            AS_OF.replace(tzinfo=None),
            AS_OF.replace(tzinfo=None),
        ],
    )


def test_conflict_diagnostic_distinguishes_competing_events(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    _insert_sector_path(
        store, candidate_id="up", event_id="event-up", direction=1
    )
    _insert_sector_path(
        store, candidate_id="down", event_id="event-down", direction=-1
    )
    result = FinancialBridgeCalibrator(store, "run").conflict_diagnostics(
        sector_run_id="sector-test",
        ticker_sectors={"SUZB3": "PAPEL_CELULOSE"},
        as_of_timestamp=AS_OF,
    )
    assert len(result) == 1
    assert result[0].classification == "LEGITIMATE_COMPETING_HYPOTHESES"
    assert result[0].decision_mode_status == "BLOCKED"
    assert result[0].resolution_method == "NONE"
    assert {item.factor_direction for item in result[0].paths} == {-1, 1}
    store.close()


def test_same_event_and_horizon_opposite_direction_is_probable_defect(
    tmp_path,
) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    _insert_sector_path(
        store, candidate_id="up", event_id="same-event", direction=1
    )
    _insert_sector_path(
        store, candidate_id="down", event_id="same-event", direction=-1
    )
    result = FinancialBridgeCalibrator(store, "run").conflict_diagnostics(
        sector_run_id="sector-test",
        ticker_sectors={"KLBN11": "PAPEL_CELULOSE"},
        as_of_timestamp=AS_OF,
    )
    assert result[0].classification == "PROBABLE_GRAPH_OR_PROPAGATION_DEFECT"
    store.close()


def test_calibration_mode_runs_positive_and_negative_shocks(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    observations = [
        BridgeReplayObservation(
            ticker="MGLU3",
            bridge="NET_INTEREST_CASH_EFFECT",
            period_end=date(2024, quarter * 3, 28),
            factor_change=.01,
            financial_change=-10,
            predicted_change=-9,
            error=-1,
            source_ids=["CVM", "BCB"],
        )
        for quarter in range(1, 5)
    ]
    observations.append(observations[-1].model_copy(
        update={"period_end": date(2025, 3, 31)}
    ))
    calibration = BridgeCalibrationResult(
        calibration_id="cal",
        ticker="MGLU3",
        bridge="NET_INTEREST_CASH_EFFECT",
        mode="CALIBRATION_MODE",
        observations=observations,
        parameters={
            "observed_slope": -1000,
            "average_gross_debt_proxy": 1200,
            "average_standardized_cash_proxy": 200,
            "quarter_horizon": .25,
        },
        parameter_ranges={"observed_slope": [-750, -1000, -1250]},
        heuristic_sensitivity_band={"observed_slope": [-750, -1000, -1250]},
        sensitivity_band_type="HEURISTIC_SENSITIVITY_BAND",
        mean_absolute_error=1,
        in_sample_mae=1,
        validation_method="STRUCTURAL_FORMULA_ONLY",
        observation_count=5,
        calibration_type="STRUCTURAL_SENSITIVITY",
        validation_gate_passed=False,
        validation_failures=["NO_EMPIRICAL_PARAMETER_ESTIMATION"],
        confidence=.5,
        calibration_status="STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE",
        methodology_version="test",
        run_id="run",
    )
    rows = FinancialBridgeCalibrator(store, "run").controlled_shocks(
        calibration
    )
    assert {row["shock"] for row in rows} == {-200, -100, -50, 0, 50, 100, 200}
    neutral = next(row for row in rows if row["shock"] == 0)
    assert neutral["estimated_financial_change"] == 0
    assert all(row["mode"] == "CALIBRATION_MODE" for row in rows)
    store.close()


def test_normalized_fcf_stays_separate_and_evidenced(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    calibrator = FinancialBridgeCalibrator(store, "run")
    calibrator.quarterly_financials = lambda _ticker, _as_of=None: pd.DataFrame({
        "operating_cash_flow": [10, 12, 11, 13, 9, 14, 12, 11],
        "document_id": [f"DOC-{item}" for item in range(8)],
    })
    evidence = SimpleNamespace(
        field_name="ttm_operating_cash_flow", source_ids=["DFP", "ITR"]
    )
    baseline = SimpleNamespace(
        baseline_id="base",
        ticker="MGLU3",
        as_of_timestamp=AS_OF,
        latest_quarter=date(2026, 3, 31),
        ttm_operating_cash_flow=100,
        ttm_capex=-20,
        ttm_fcf=80,
        field_evidence=[evidence],
    )
    result = calibrator.normalize_cash_flow(baseline)
    assert result.levered_fcf_proxy == 80
    assert result.normalized_levered_fcf == 26
    assert result.statistical_normalized_fcf_proxy == 26
    assert result.normalization_status == "NOT_VALUATION_READY"
    assert result.dcf_eligible is False
    assert all(item.source_ids for item in result.adjustments)
    assert result.confidence < 0.6
    store.close()


def test_two_factor_replay_recovers_intercept_fx_and_pulp_coefficients() -> None:
    first = [-.2, -.1, 0, .1, .2]
    second = [.1, -.2, .2, -.1, 0]
    target = [
        1 + 2 * fx + 3 * pulp
        for fx, pulp in zip(first, second, strict=True)
    ]
    intercept, coefficients = FinancialBridgeCalibrator._multiple_regression(
        first, second, target
    )
    assert intercept == pytest.approx(1.0)
    assert coefficients == pytest.approx((2.0, 3.0))


def test_leave_one_out_persists_error_and_sign_stability() -> None:
    first = [-.3, -.2, -.1, .1, .2, .3]
    second = [.2, -.1, .3, -.2, .1, -.3]
    target = [1 + 2 * fx + 3 * pulp for fx, pulp in zip(first, second, strict=True)]
    _, full = FinancialBridgeCalibrator._multiple_regression(first, second, target)

    predictions, stability = FinancialBridgeCalibrator._leave_one_out_predictions(
        first, second, target, full
    )

    assert predictions == pytest.approx(target)
    assert stability == pytest.approx((1.0, 1.0))


def test_structural_sensitivity_cannot_pass_validation_gate(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    frame = pd.DataFrame({
        "period_end": pd.date_range("2024-03-31", periods=5, freq="QE"),
        "factor_change": [.01] * 5,
        "financial_change": [-10.0] * 5,
        "predicted_change": [-9.0] * 5,
        "document_id": [f"DOC-{index}" for index in range(5)],
    })
    result = FinancialBridgeCalibrator(
        store, "run"
    )._calibration_from_predictions(
        "MGLU3",
        "NET_INTEREST_CASH_EFFECT",
        frame,
        parameters={"observed_slope": -1000.0},
        missing_drivers=["EFFECTIVE_FLOATING_DEBT_SHARE"],
        calibration_type="STRUCTURAL_SENSITIVITY",
    )

    assert result.calibration_type == "STRUCTURAL_SENSITIVITY"
    assert result.validation_gate_passed is False
    assert "NO_EMPIRICAL_PARAMETER_ESTIMATION" in result.validation_failures
    assert result.calibration_status == "STRUCTURAL_SENSITIVITY_LOW_CONFIDENCE"
    store.close()


def test_empirical_in_sample_gate_preserves_oos_failures(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    frame = pd.DataFrame({
        "period_end": pd.date_range("2024-03-31", periods=5, freq="QE"),
        "factor_change": [.01, .02, .03, .04, .05],
        "secondary_factor_change": [.02, .01, -.01, -.02, .03],
        "financial_change": [.2, -.2, .3, -.3, .4],
        "predicted_change": [0.0] * 5,
        "out_of_sample_predicted_change": [1.0] * 5,
        "out_of_sample_error": [-.8, -1.2, -.7, -1.3, -.6],
        "document_id": [f"DOC-{index}" for index in range(5)],
    })
    result = FinancialBridgeCalibrator(
        store, "run"
    )._calibration_from_predictions(
        "SUZB3",
        "FX_OPERATING_REVENUE",
        frame,
        parameters={"intercept": 0.0, "fx_observed_slope": 1.0},
        missing_drivers=["DISCLOSED_VOLUME_HISTORY"],
        calibration_type="EMPIRICAL_IN_SAMPLE",
        validation_method="LEAVE_ONE_OUT",
        coefficient_sign_stability={"fx_observed_slope": 0.6},
    )

    assert result.calibration_type == "EMPIRICAL_IN_SAMPLE"
    assert result.out_of_sample_mae is not None
    assert result.validation_gate_passed is False
    assert "OUT_OF_SAMPLE_ERROR_TOO_HIGH" in result.validation_failures
    assert "COEFFICIENT_SIGN_UNSTABLE" in result.validation_failures
    store.close()


def test_financial_outcome_labels_follow_calculated_result() -> None:
    rows = [
        {
            "ticker": "TEST3",
            "bridge": "FX_OPERATING_REVENUE",
            "shock": 20.0,
            "estimated_financial_change": -30.0,
        },
        {
            "ticker": "TEST3",
            "bridge": "FX_OPERATING_REVENUE",
            "shock": 0.0,
            "estimated_financial_change": 0.0,
        },
        {
            "ticker": "TEST3",
            "bridge": "FX_OPERATING_REVENUE",
            "shock": 10.0,
            "estimated_financial_change": 15.0,
        },
    ]

    result = FinancialCalibrationPilot._outcome_intervals(rows)

    assert result[0]["pessimistic"]["shock"] == 20.0
    assert result[0]["base"]["shock"] == 0.0
    assert result[0]["optimistic"]["shock"] == 10.0
