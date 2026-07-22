from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import duckdb
from pydantic import BaseModel

class DatabaseStore:
    """
    Banco de dados de auditoria e persistencia real usando DuckDB.
    """
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(db_path))
        self._init_tables()

    def _init_tables(self) -> None:
        # 1. Audit Events
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                run_id VARCHAR,
                entity_type VARCHAR,
                entity_id VARCHAR,
                payload_json VARCHAR,
                inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # 2. Evidence
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id VARCHAR PRIMARY KEY,
                source_id VARCHAR,
                source_tier INTEGER,
                claim VARCHAR,
                published_at TIMESTAMP,
                observed_at TIMESTAMP,
                effective_date TIMESTAMP,
                url VARCHAR,
                raw_checksum VARCHAR,
                confidence DOUBLE,
                run_id VARCHAR
            );
        """)
        # 3. Asset Snapshots
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS asset_snapshots (
                ticker VARCHAR,
                asset_class VARCHAR,
                as_of TIMESTAMP,
                price DOUBLE,
                avg_daily_volume_brl DOUBLE,
                sector VARCHAR,
                metrics_json VARCHAR,
                inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (ticker, as_of)
            );
        """)
        # 4. Ingestion Runs
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                run_id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP,
                status VARCHAR NOT NULL,
                received_count INTEGER DEFAULT 0,
                valid_count INTEGER DEFAULT 0,
                rejected_count INTEGER DEFAULT 0,
                error_message VARCHAR
            );
        """)
        # 5. Macro Observations
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_observations (
                source VARCHAR NOT NULL,
                series_code VARCHAR NOT NULL,
                indicator VARCHAR NOT NULL,
                reference_date DATE NOT NULL,
                observed_at TIMESTAMP NOT NULL,
                available_at TIMESTAMP,
                value DECIMAL(28, 10) NOT NULL,
                unit VARCHAR NOT NULL,
                frequency VARCHAR NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                raw_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL,
                PRIMARY KEY (
                    source,
                    series_code,
                    reference_date,
                    raw_checksum
                )
            );
        """)
        # 6. Market Expectations (BCB Focus)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS market_expectations (
                source VARCHAR NOT NULL,
                indicator VARCHAR NOT NULL,
                reference_date DATE NOT NULL,
                target_period VARCHAR NOT NULL,
                statistic VARCHAR NOT NULL,
                value DECIMAL(28, 10) NOT NULL,
                base_calculation INTEGER,
                observed_at TIMESTAMP NOT NULL,
                raw_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL,
                PRIMARY KEY (
                    source,
                    indicator,
                    reference_date,
                    target_period,
                    statistic,
                    raw_checksum
                )
            );
        """)

    def start_ingestion_run(self, run_id: str, source: str) -> None:
        self.connection.execute(
            "INSERT INTO ingestion_runs (run_id, source, started_at, status) VALUES (?, ?, ?, ?)",
            [run_id, source, datetime.now(timezone.utc), "RUNNING"]
        )

    def finish_ingestion_run(self, run_id: str, status: str, received: int, valid: int, rejected: int, error: str = "") -> None:
        self.connection.execute(
            """
            UPDATE ingestion_runs 
            SET finished_at = ?, status = ?, received_count = ?, valid_count = ?, rejected_count = ?, error_message = ?
            WHERE run_id = ?
            """,
            [datetime.now(timezone.utc), status, received, valid, rejected, error, run_id]
        )

    def save_macro_observation(self, obs: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO macro_observations (
                source, series_code, indicator, reference_date, observed_at, available_at,
                value, unit, frequency, revision, raw_checksum, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                obs["source"], obs["series_code"], obs["indicator"], obs["reference_date"],
                obs["observed_at"], obs.get("available_at"), obs["value"], obs["unit"],
                obs["frequency"], obs.get("revision", 0), obs["raw_checksum"], obs["ingestion_run_id"]
            ]
        )

    def save_market_expectation(self, exp: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO market_expectations (
                source, indicator, reference_date, target_period, statistic,
                value, base_calculation, observed_at, raw_checksum, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                exp["source"], exp["indicator"], exp["reference_date"], exp["target_period"],
                exp["statistic"], exp["value"], exp.get("base_calculation"), exp["observed_at"],
                exp["raw_checksum"], exp["ingestion_run_id"]
            ]
        )

    def save_asset_snapshot(self, snapshot: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO asset_snapshots(
                ticker, asset_class, as_of, price, avg_daily_volume_brl, sector, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snapshot.get("ticker"),
                str(snapshot.get("asset_class")),
                snapshot.get("as_of"),
                snapshot.get("price"),
                snapshot.get("avg_daily_volume_brl"),
                snapshot.get("sector"),
                json.dumps(snapshot.get("metrics", {}), ensure_ascii=False)
            ]
        )

    def count_macro_observations(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM macro_observations").fetchone()
        return res[0] if res else 0

    def count_market_expectations(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM market_expectations").fetchone()
        return res[0] if res else 0

    def count_snapshots(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM asset_snapshots").fetchone()
        return res[0] if res else 0

    def close(self) -> None:
        self.connection.close()
