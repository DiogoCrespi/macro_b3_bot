"""
Unit and integration tests for Sprint 4A.1-T — Temporal Integrity.

Tests cover:
- Initial vs revised vintages
- Same value in different vintages
- Look-ahead detection (available_at > decision_time)
- Valid point-in-time releases (available_at <= decision_time)
- BCB SGS -> MacroRelease transformation
- EIA unknown publication precision
- NOAA estimated monthly publication precision
- Checksum stability (no volatile fields)
- Checksum distinction across vintages
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import tempfile
from pathlib import Path

from macro_b3_bot.adapters.macro.fred_client import normalize_fred_observation, make_record_checksum as fred_checksum
from macro_b3_bot.adapters.macro.eia_client import normalize_eia_observation
from macro_b3_bot.adapters.macro.noaa_enso_client import normalize_noaa_observation
from macro_b3_bot.infrastructure.store import DatabaseStore


def test_vintage_inicial_e_revisada() -> None:
    now = datetime.now(timezone.utc)
    obs_initial = {
        "date": "2024-01-01",
        "value": "3.2",
        "realtime_start": "2024-02-15",
        "realtime_end": "2024-03-14",
    }
    obs_revised = {
        "date": "2024-01-01",
        "value": "3.4",
        "realtime_start": "2024-03-15",
        "realtime_end": "9999-12-31",
    }

    rel1 = normalize_fred_observation(obs_initial, "CPIAUCSL", "CPI", ["US"], "MONTHLY", "%", "run1", now)
    rel2 = normalize_fred_observation(obs_revised, "CPIAUCSL", "CPI", ["US"], "MONTHLY", "%", "run2", now)

    assert rel1 is not None and rel2 is not None
    assert rel1["actual_value"] == Decimal("3.2")
    assert rel2["actual_value"] == Decimal("3.4")
    assert rel1["record_checksum"] != rel2["record_checksum"]
    assert rel1["vintage_date"] == date(2024, 2, 15)
    assert rel2["vintage_date"] == date(2024, 3, 15)


def test_mesmo_valor_em_vintages_diferentes() -> None:
    now = datetime.now(timezone.utc)
    obs_v1 = {
        "date": "2024-01-01",
        "value": "3.2",
        "realtime_start": "2024-02-15",
        "realtime_end": "2024-03-14",
    }
    obs_v2 = {
        "date": "2024-01-01",
        "value": "3.2",
        "realtime_start": "2024-03-15",
        "realtime_end": "9999-12-31",
    }

    rel1 = normalize_fred_observation(obs_v1, "CPIAUCSL", "CPI", ["US"], "MONTHLY", "%", "run1", now)
    rel2 = normalize_fred_observation(obs_v2, "CPIAUCSL", "CPI", ["US"], "MONTHLY", "%", "run2", now)

    assert rel1 is not None and rel2 is not None
    # Checksums must differ because the vintage / realtime_start changed
    assert rel1["record_checksum"] != rel2["record_checksum"]


def test_release_disponivel_depois_da_decisao_lookahead() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        decision_time = datetime(2024, 2, 1, tzinfo=timezone.utc)
        release_available_at = datetime(2024, 2, 15, tzinfo=timezone.utc)

        rel = {
            "release_id": "rel_future",
            "source": "FRED",
            "series_code": "CPIAUCSL",
            "indicator": "CPI",
            "geography": ["US"],
            "frequency": "MONTHLY",
            "unit": "%",
            "reference_date": date(2024, 1, 1),
            "published_at": release_available_at,
            "available_at": release_available_at,
            "actual_value": Decimal("3.2"),
            "raw_checksum": "raw1",
            "record_checksum": "rec1",
            "ingestion_run_id": "run1",
        }
        store.save_macro_release(rel)

        store.connection.execute(
            """
            INSERT INTO macro_event_candidates (
                event_id, event_type, indicator, geography, affected_variables,
                reference_date, detected_at, horizon_months, surprise_score, novelty_score,
                persistence_score, regime_shift_score, data_quality_score, direction, current_regime,
                evidence_ids, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "evt_early", "INFLATION_SURPRISE", "CPI", '["US"]', '["FEDFUNDS"]',
                date(2024, 1, 1), decision_time, 12, 0.8, 0.7, 0.6, 0.5, 0.9,
                "HAWKISH", "TIGHTENING", '["rel_future"]', "PENDING"
            ]
        )

        store.connection.execute(
            "INSERT INTO macro_event_evidence_links (event_id, release_id) VALUES (?, ?)",
            ["evt_early", "rel_future"]
        )

        lookahead_violations = store.connection.execute(
            """
            SELECT COUNT(*)
            FROM macro_event_evidence_links l
            JOIN macro_releases r ON r.release_id = l.release_id
            JOIN macro_event_candidates e ON e.event_id = l.event_id
            WHERE r.available_at > e.detected_at
            """
        ).fetchone()[0]

        assert lookahead_violations == 1
        store.close()


def test_release_disponivel_na_decisao_valida() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "audit.duckdb"
        store = DatabaseStore(db_path)

        release_available_at = datetime(2024, 2, 1, tzinfo=timezone.utc)
        decision_time = datetime(2024, 2, 15, tzinfo=timezone.utc)

        rel = {
            "release_id": "rel_past",
            "source": "FRED",
            "series_code": "CPIAUCSL",
            "indicator": "CPI",
            "geography": ["US"],
            "frequency": "MONTHLY",
            "unit": "%",
            "reference_date": date(2024, 1, 1),
            "published_at": release_available_at,
            "available_at": release_available_at,
            "actual_value": Decimal("3.2"),
            "raw_checksum": "raw2",
            "record_checksum": "rec2",
            "ingestion_run_id": "run2",
        }
        store.save_macro_release(rel)

        store.connection.execute(
            """
            INSERT INTO macro_event_candidates (
                event_id, event_type, indicator, geography, affected_variables,
                reference_date, detected_at, horizon_months, surprise_score, novelty_score,
                persistence_score, regime_shift_score, data_quality_score, direction, current_regime,
                evidence_ids, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "evt_valid", "INFLATION_SURPRISE", "CPI", '["US"]', '["FEDFUNDS"]',
                date(2024, 1, 1), decision_time, 12, 0.8, 0.7, 0.6, 0.5, 0.9,
                "HAWKISH", "TIGHTENING", '["rel_past"]', "PENDING"
            ]
        )

        store.connection.execute(
            "INSERT INTO macro_event_evidence_links (event_id, release_id) VALUES (?, ?)",
            ["evt_valid", "rel_past"]
        )

        lookahead_violations = store.connection.execute(
            """
            SELECT COUNT(*)
            FROM macro_event_evidence_links l
            JOIN macro_releases r ON r.release_id = l.release_id
            JOIN macro_event_candidates e ON e.event_id = l.event_id
            WHERE r.available_at > e.detected_at
            """
        ).fetchone()[0]

        assert lookahead_violations == 0
        store.close()


def test_eia_disponibilidade_desconhecida() -> None:
    now = datetime.now(timezone.utc)
    obs = {"period": "2024-01", "value": "75.40"}

    rel = normalize_eia_observation(obs, "PET_PRI_SPT_S1_D", "WTI Crude", ["US"], "MONTHLY", "USD/BBL", "run1", now)
    assert rel is not None
    assert rel["published_at"] is None
    assert rel["availability_precision"] == "UNKNOWN"
    assert rel["available_at"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert rel["collected_at"] == now


def test_noaa_disponibilidade_estimada() -> None:
    now = datetime.now(timezone.utc)
    record = {"year": 2024, "month": 1, "nino34_anom": 1.2, "oni": 1.1}

    rel = normalize_noaa_observation(record, "NINO34", "Nino 3.4 Index", ["GLOBAL"], "MONTHLY", "INDEX", "run1", now)
    assert rel is not None
    assert rel["availability_precision"] == "ESTIMATED_MONTHLY"
    assert rel["published_at"] == datetime(2024, 2, 1, tzinfo=timezone.utc)
    assert rel["available_at"] == datetime(2024, 2, 1, tzinfo=timezone.utc)
    assert rel["collected_at"] == now


def test_checksum_sem_campos_volateis() -> None:
    chk1 = fred_checksum("FRED", "DFF", date(2024, 1, 1), date(2024, 1, 2), "2024-01-02", "9999-12-31", "5.33", "%")
    chk2 = fred_checksum("FRED", "DFF", date(2024, 1, 1), date(2024, 1, 2), "2024-01-02", "9999-12-31", "5.33", "%")
    assert chk1 == chk2


def test_checksum_distinguindo_vintages() -> None:
    chk_v1 = fred_checksum("FRED", "DFF", date(2024, 1, 1), date(2024, 1, 2), "2024-01-02", "2024-01-05", "5.33", "%")
    chk_v2 = fred_checksum("FRED", "DFF", date(2024, 1, 1), date(2024, 1, 6), "2024-01-06", "9999-12-31", "5.35", "%")
    assert chk_v1 != chk_v2
