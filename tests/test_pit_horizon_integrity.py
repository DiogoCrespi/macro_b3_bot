"""Sprint 4D.3B PIT and Horizon Integrity tests."""
from datetime import datetime, timezone
import pandas as pd

from macro_b3_bot.application.calibrate_financial_bridges import (
    FinancialBridgeCalibrator,
)
from macro_b3_bot.infrastructure.store import DatabaseStore


AS_OF = datetime(2026, 7, 22, 23, 59, 59, tzinfo=timezone.utc)


def test_pit_filtering_and_horizon_metadata(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "pit_test.duckdb")
    calibrator = FinancialBridgeCalibrator(store, "run_pit_test")

    # Verify methodology version updated
    assert calibrator.methodology_version == "4D.3B-pit-horizon-integrity-v1"

    # Insert mock map
    store.connection.execute(
        """
        INSERT INTO company_ticker_map (
            ticker,cvm_code,cnpj,mapping_source,confidence,validated,created_at,
            legal_name,valid_from,review_status,evidence_id,mapping_version
        ) VALUES ('TEST3','123456','00','test',1,TRUE,TIMESTAMP '2025-01-01',
                  'Test SA',DATE '2020-01-01','VALIDATED','map','v1')
        """
    )
    # Insert cvm_documents with different filing dates
    store.connection.execute(
        """
        INSERT INTO cvm_documents (document_id, document_type, cvm_code, cnpj, reference_date, received_at, filing_available_at, version, raw_zip_checksum, ingestion_run_id)
        VALUES 
        ('DOC-1', 'ITR', '123456', '00000000000191', '2025-03-31', '2025-05-10 10:00:00', '2025-05-10 10:00:00', 1, 'hash1', 'run1'),
        ('DOC-2', 'ITR', '123456', '00000000000191', '2025-03-31', '2025-05-15 10:00:00', '2025-05-15 10:00:00', 2, 'hash2', 'run1'),
        ('DOC-3', 'ITR', '123456', '00000000000191', '2025-06-30', '2025-08-10 10:00:00', '2025-08-10 10:00:00', 1, 'hash3', 'run1')
        """
    )
    # Insert lines for DOC-1, DOC-2, and DOC-3
    for doc_id, val, ref in [('DOC-1', 100, '2025-03-31'), ('DOC-2', 120, '2025-03-31'), ('DOC-3', 200, '2025-06-30')]:
        store.connection.execute(
            """
            INSERT INTO financial_statement_lines VALUES (
                ?,'DRE','CONSOLIDATED','ÚLTIMO','3.01','Receita',?,'BRL',1,
                DATE '2025-01-01', CAST(? AS DATE), ?
            )
            """,
            [doc_id, val, ref, f"{doc_id}-3.01"],
        )

    # Cutoff BEFORE DOC-2 (2025-05-12)
    df_early = calibrator.quarterly_financials('TEST3', datetime(2025, 5, 12))
    assert len(df_early) == 1
    assert df_early.iloc[0]["document_id"] == "DOC-1"
    assert df_early.iloc[0]["revenue"] == 100.0

    # Cutoff AFTER DOC-2 (2025-05-20) -> DOC-2 supersedes DOC-1 for period 2025-03-31
    df_late = calibrator.quarterly_financials('TEST3', datetime(2025, 5, 20))
    assert len(df_late) == 1
    assert df_late.iloc[0]["document_id"] == "DOC-2"
    assert df_late.iloc[0]["revenue"] == 120.0

    store.close()


def test_loo_cross_validation_does_not_promote_to_out_of_sample(tmp_path) -> None:
    store = DatabaseStore(tmp_path / "loo_test.duckdb")
    calibrator = FinancialBridgeCalibrator(store, "test")

    frame = pd.DataFrame({
        "period_end": pd.date_range("2024-03-31", periods=6, freq="QE"),
        "factor_change": [.01, .02, .03, .04, .05, .06],
        "secondary_factor_change": [.01, .02, .01, .02, .01, .02],
        "financial_change": [.02, .04, .06, .08, .10, .12],
        "predicted_change": [.02, .04, .06, .08, .10, .12],
        "out_of_sample_predicted_change": [.02, .04, .06, .08, .10, .12],
        "out_of_sample_error": [0.0] * 6,
        "document_id": [f"DOC-{i}" for i in range(6)],
    })

    res = calibrator._calibration_from_predictions(
        "SUZB3",
        "FX_OPERATING_REVENUE",
        frame,
        parameters={"fx_observed_slope": 2.0},
        missing_drivers=[],
        calibration_type="EMPIRICAL_IN_SAMPLE",
        validation_method="EMPIRICAL_LOO_CROSS_VALIDATED",
        coefficient_sign_stability={"fx_observed_slope": 1.0},
        calibration_horizon="QUARTERLY",
        financial_target_period="QUARTERLY",
        monetary_base_period="QUARTERLY",
        annualization_method="NONE",
    )

    assert res.validation_method == "EMPIRICAL_LOO_CROSS_VALIDATED"
    # Even if validation_gate_passed is True, LOO cross validation must NOT set calibration_type to EMPIRICAL_OUT_OF_SAMPLE_VALIDATED
    assert res.calibration_type != "EMPIRICAL_OUT_OF_SAMPLE_VALIDATED"
    assert res.calibration_type == "EMPIRICAL_IN_SAMPLE"
    assert res.calibration_horizon == "QUARTERLY"
    assert res.financial_target_period == "QUARTERLY"
    assert res.monetary_base_period == "QUARTERLY"
    assert res.annualization_method == "NONE"


def test_expanding_window_predictions() -> None:
    first = [-.3, -.2, -.1, .1, .2, .3]
    second = [.2, -.1, .3, -.2, .1, -.3]
    target = [1 + 2 * fx + 3 * pulp for fx, pulp in zip(first, second, strict=True)]
    _, full = FinancialBridgeCalibrator._multiple_regression(first, second, target)

    preds, stability = FinancialBridgeCalibrator._expanding_window_predictions(
        first, second, target, full, min_train_size=4
    )

    assert len(preds) == 6
    assert preds[0] is None
    assert preds[3] is None
    assert preds[4] is not None
    assert stability[0] >= 0.8
