from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import duckdb
from pydantic import BaseModel

class DatabaseStore:
    """
    Banco de dados de auditoria e persistência real usando DuckDB.
    Suporta BCB, b3_screener e demonstrações financeiras da CVM (ITR/DFP).
    """
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(db_path))
        self._init_tables()
        self._init_views()

    def _init_tables(self) -> None:
        # Audit Events
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                run_id VARCHAR,
                entity_type VARCHAR,
                entity_id VARCHAR,
                payload_json VARCHAR,
                inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Evidence
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
        # Asset Snapshots
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
        # Ingestion Runs
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
        # Macro Observations (BCB SGS)
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
        # Market Expectations (BCB Focus)
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
        # CVM Companies
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS cvm_companies (
                cvm_code VARCHAR NOT NULL,
                cnpj VARCHAR NOT NULL,
                legal_name VARCHAR NOT NULL,
                trading_name VARCHAR,
                registration_status VARCHAR,
                registration_date DATE,
                cancellation_date DATE,
                category VARCHAR,
                collected_at TIMESTAMP NOT NULL,
                record_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL,
                PRIMARY KEY (cvm_code, record_checksum)
            );
        """)
        # Company Ticker Map
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS company_ticker_map (
                ticker VARCHAR NOT NULL,
                cvm_code VARCHAR,
                cnpj VARCHAR,
                mapping_source VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                validated BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY (ticker, cnpj)
            );
        """)
        # CVM Documents (ITR / DFP)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS cvm_documents (
                document_id VARCHAR PRIMARY KEY,
                document_type VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                cnpj VARCHAR NOT NULL,
                reference_date DATE NOT NULL,
                received_at TIMESTAMP NOT NULL,
                version INTEGER NOT NULL,
                raw_zip_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL
            );
        """)
        # Financial Statement Lines (Linhas Contábeis DRE, BPA, BPP, DFC)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS financial_statement_lines (
                document_id VARCHAR NOT NULL,
                statement_type VARCHAR NOT NULL,
                scope VARCHAR NOT NULL,
                fiscal_order VARCHAR NOT NULL,
                account_code VARCHAR NOT NULL,
                account_description VARCHAR NOT NULL,
                value DECIMAL(28, 4) NOT NULL,
                currency VARCHAR NOT NULL,
                scale INTEGER NOT NULL,
                start_date DATE,
                end_date DATE NOT NULL,
                record_checksum VARCHAR NOT NULL,
                PRIMARY KEY (
                    document_id,
                    statement_type,
                    scope,
                    fiscal_order,
                    account_code,
                    record_checksum
                )
            );
        """)

    def _init_views(self) -> None:
        # Visão da versão mais recente dos documentos da CVM
        self.connection.execute("""
            CREATE OR REPLACE VIEW latest_cvm_documents AS
            SELECT *
            FROM cvm_documents
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY document_type, cvm_code, reference_date
                ORDER BY version DESC, received_at DESC
            ) = 1;
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

    def save_macro_observation(self, obs: dict) -> bool:
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM macro_observations WHERE source = ? AND series_code = ? AND reference_date = ? AND raw_checksum = ?",
            [obs["source"], obs["series_code"], obs["reference_date"], obs["raw_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO macro_observations (
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
        return True

    def save_market_expectation(self, exp: dict) -> bool:
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM market_expectations WHERE source = ? AND indicator = ? AND reference_date = ? AND target_period = ? AND statistic = ? AND raw_checksum = ?",
            [exp["source"], exp["indicator"], exp["reference_date"], exp["target_period"], exp["statistic"], exp["raw_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO market_expectations (
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
        return True

    def save_cvm_company(self, company: dict) -> bool:
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM cvm_companies WHERE cvm_code = ? AND record_checksum = ?",
            [company["cvm_code"], company["record_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO cvm_companies (
                cvm_code, cnpj, legal_name, trading_name, registration_status,
                registration_date, cancellation_date, category, collected_at, record_checksum, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                company["cvm_code"], company["cnpj"], company["legal_name"], company.get("trading_name"),
                company.get("registration_status"), company.get("registration_date"), company.get("cancellation_date"),
                company.get("category"), company["collected_at"], company["record_checksum"], company["ingestion_run_id"]
            ]
        )
        return True

    def save_ticker_mapping(self, mapping: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO company_ticker_map (
                ticker, cvm_code, cnpj, mapping_source, confidence, validated, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                mapping["ticker"], mapping.get("cvm_code"), mapping.get("cnpj"),
                mapping["mapping_source"], mapping["confidence"], mapping.get("validated", False),
                datetime.now(timezone.utc)
            ]
        )

    def save_cvm_document(self, doc: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO cvm_documents (
                document_id, document_type, cvm_code, cnpj, reference_date,
                received_at, version, raw_zip_checksum, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                doc["document_id"], doc["document_type"], doc["cvm_code"], doc["cnpj"],
                doc["reference_date"], doc["received_at"], doc["version"],
                doc["raw_zip_checksum"], doc["ingestion_run_id"]
            ]
        )

    def save_financial_line(self, line: dict) -> bool:
        existing = self.connection.execute(
            """
            SELECT COUNT(*) FROM financial_statement_lines
            WHERE document_id = ? AND statement_type = ? AND scope = ? AND fiscal_order = ? AND account_code = ? AND record_checksum = ?
            """,
            [line["document_id"], line["statement_type"], line["scope"], line["fiscal_order"], line["account_code"], line["record_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO financial_statement_lines (
                document_id, statement_type, scope, fiscal_order, account_code,
                account_description, value, currency, scale, start_date, end_date, record_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                line["document_id"], line["statement_type"], line["scope"], line["fiscal_order"],
                line["account_code"], line["account_description"], line["value"], line["currency"],
                line["scale"], line.get("start_date"), line["end_date"], line["record_checksum"]
            ]
        )
        return True

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

    def count_cvm_companies(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM cvm_companies").fetchone()
        return res[0] if res else 0

    def count_ticker_mappings(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM company_ticker_map").fetchone()
        return res[0] if res else 0

    def count_cvm_documents(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM cvm_documents").fetchone()
        return res[0] if res else 0

    def count_financial_lines(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM financial_statement_lines").fetchone()
        return res[0] if res else 0

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
