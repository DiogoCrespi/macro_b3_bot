"""Sprint 4D.3 conflict, calibration-mode and normalized-FCF tests."""
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from macro_b3_bot.application.calibrate_financial_bridges import (
    FinancialBridgeCalibrator,
)
from macro_b3_bot.application.run_financial_calibration_pilot import (
    FinancialCalibrationPilot,
)
from macro_b3_bot.domain.financial_bridge_models import (
    BridgeCalibrationResult,
    BridgeReplayObservation,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


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
            "average_gross_floating_debt": 1200,
            "average_cash_sensitive_to_rate": 200,
            "quarter_horizon": .25,
            "repricing_factor": 1,
        },
        parameter_ranges={"observed_slope": [-750, -1000, -1250]},
        mean_absolute_error=1,
        confidence=.5,
        calibration_status="COMPANY_CALIBRATED",
        methodology_version="test",
        run_id="run",
    )
    rows = FinancialBridgeCalibrator(store, "run").controlled_shocks(
        calibration
    )
    assert {row["shock"] > 0 for row in rows} == {True, False}
    assert all(row["mode"] == "CALIBRATION_MODE" for row in rows)
    store.close()


def test_normalized_fcf_stays_separate_and_evidenced(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "calibration.duckdb")
    calibrator = FinancialBridgeCalibrator(store, "run")
    calibrator.quarterly_financials = lambda _ticker: pd.DataFrame({
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
    assert all(item.source_ids for item in result.adjustments)
    assert result.confidence < 0.6
    store.close()


def test_two_factor_replay_recovers_fx_and_pulp_coefficients() -> None:
    first = [-.2, -.1, 0, .1, .2]
    second = [.1, -.2, .2, -.1, 0]
    target = [
        2 * fx + 3 * pulp
        for fx, pulp in zip(first, second, strict=True)
    ]
    coefficients = FinancialBridgeCalibrator._multiple_slopes(
        first, second, target
    )
    assert coefficients == pytest.approx((2.0, 3.0))


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
            "shock": -5.0,
            "estimated_financial_change": 5.0,
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
    assert result[0]["base"]["shock"] == -5.0
    assert result[0]["optimistic"]["shock"] == 10.0
