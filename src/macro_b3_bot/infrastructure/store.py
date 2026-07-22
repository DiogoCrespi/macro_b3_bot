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
        # Financial Statement Lines
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
        # IPE Document Index
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS ipe_document_index (
                document_id VARCHAR PRIMARY KEY,
                cvm_code VARCHAR NOT NULL,
                company_name VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                document_type VARCHAR,
                subject VARCHAR,
                reference_date DATE,
                delivery_date TIMESTAMP NOT NULL,
                protocol VARCHAR,
                version INTEGER NOT NULL,
                source_url VARCHAR,
                raw_index_checksum VARCHAR NOT NULL,
                record_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL
            );
        """)
        # IPE Processing Queue
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS ipe_processing_queue (
                document_id VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                priority_score DOUBLE NOT NULL,
                materiality_score DOUBLE,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error VARCHAR,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
        """)
        # IPE Document Versions
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS ipe_document_versions (
                document_id VARCHAR NOT NULL,
                version INTEGER NOT NULL,
                delivery_date TIMESTAMP NOT NULL,
                source_url VARCHAR,
                document_checksum VARCHAR,
                collected_at TIMESTAMP,
                PRIMARY KEY (document_id, version)
            );
        """)
        # Downloaded Documents
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS downloaded_documents (
                document_id VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                http_status INTEGER NOT NULL,
                mime_type VARCHAR NOT NULL,
                file_extension VARCHAR,
                file_size_bytes BIGINT NOT NULL,
                raw_path VARCHAR NOT NULL,
                document_checksum VARCHAR NOT NULL,
                downloaded_at TIMESTAMP NOT NULL,
                ingestion_run_id VARCHAR NOT NULL,
                PRIMARY KEY (document_id, document_checksum)
            );
        """)
        # Extracted Documents
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS extracted_documents (
                document_id VARCHAR NOT NULL,
                document_checksum VARCHAR NOT NULL,
                extraction_method VARCHAR NOT NULL,
                extracted_text VARCHAR NOT NULL,
                text_length INTEGER NOT NULL,
                page_count INTEGER,
                language VARCHAR,
                normalized_text_checksum VARCHAR NOT NULL,
                extraction_quality DOUBLE NOT NULL,
                extracted_at TIMESTAMP NOT NULL,
                PRIMARY KEY (
                    document_id,
                    document_checksum,
                    normalized_text_checksum
                )
            );
        """)
        # Document Processing Errors
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS document_processing_errors (
                document_id VARCHAR NOT NULL,
                stage VARCHAR NOT NULL,
                error_type VARCHAR NOT NULL,
                error_message VARCHAR,
                attempt INTEGER NOT NULL,
                occurred_at TIMESTAMP NOT NULL
            );
        """)
        # Document Duplicate Links
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS document_duplicate_links (
                canonical_document_id VARCHAR NOT NULL,
                duplicate_document_id VARCHAR NOT NULL,
                duplicate_type VARCHAR NOT NULL,
                similarity DOUBLE NOT NULL,
                detected_at TIMESTAMP NOT NULL,
                PRIMARY KEY (
                    canonical_document_id,
                    duplicate_document_id
                )
            );
        """)
        # Evidence Claims
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS evidence_claims (
                claim_id VARCHAR PRIMARY KEY,
                document_id VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                ticker VARCHAR,
                claim_type VARCHAR NOT NULL,
                subject VARCHAR NOT NULL,
                predicate VARCHAR NOT NULL,
                object_text VARCHAR NOT NULL,
                numeric_value DECIMAL(28, 4),
                unit VARCHAR,
                currency VARCHAR,
                effective_date DATE,
                horizon_end DATE,
                source_page INTEGER,
                source_start INTEGER,
                source_end INTEGER,
                source_excerpt VARCHAR NOT NULL,
                extraction_method VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                created_at TIMESTAMP NOT NULL
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

    def save_cvm_document_with_status(self, doc: dict) -> tuple[bool, bool]:
        """
        Salva um documento CVM.
        Retorna (was_inserted, was_restatement).
        """
        existing = self.connection.execute(
            "SELECT document_id, version FROM cvm_documents WHERE document_id = ?",
            [doc["document_id"]]
        ).fetchone()

        if existing:
            return (False, False) # Duplicado idêntico (mesma versão e ID)

        # Verifica se existe outra versão do mesmo documento (reapresentação)
        has_other_version = self.connection.execute(
            "SELECT COUNT(*) FROM cvm_documents WHERE document_type = ? AND cvm_code = ? AND reference_date = ?",
            [doc["document_type"], doc["cvm_code"], doc["reference_date"]]
        ).fetchone()[0]

        self.connection.execute(
            """
            INSERT INTO cvm_documents (
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
        return (True, has_other_version > 0)

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

    def save_ipe_document_index(self, doc: dict) -> bool:
        """Salva um documento de índice IPE. Retorna True se inserido, False se já existia (duplicado)."""
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM ipe_document_index WHERE document_id = ?",
            [doc["document_id"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO ipe_document_index (
                document_id, cvm_code, company_name, category, document_type,
                subject, reference_date, delivery_date, protocol, version,
                source_url, raw_index_checksum, record_checksum, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                doc["document_id"], doc["cvm_code"], doc["company_name"], doc["category"],
                doc.get("document_type"), doc.get("subject"), doc.get("reference_date"),
                doc["delivery_date"], doc.get("protocol"), doc.get("version", 1),
                doc.get("source_url"), doc["raw_index_checksum"], doc["record_checksum"],
                doc["ingestion_run_id"]
            ]
        )
        return True

    def save_ipe_processing_state(self, state: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO ipe_processing_queue (
                document_id, status, priority_score, materiality_score,
                attempts, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                state["document_id"], state["status"], state["priority_score"],
                state.get("materiality_score"), state.get("attempts", 0),
                state.get("last_error"), datetime.now(timezone.utc), datetime.now(timezone.utc)
            ]
        )

    def count_ipe_documents(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM ipe_document_index").fetchone()
        return res[0] if res else 0

    def count_ipe_queue(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM ipe_processing_queue").fetchone()
        return res[0] if res else 0

    def save_cvm_document(self, doc: dict) -> None:
        self.save_cvm_document_with_status(doc)

    def count_snapshots(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM asset_snapshots").fetchone()
        return res[0] if res else 0

    def save_downloaded_document(self, doc: dict) -> bool:
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM downloaded_documents WHERE document_id = ? AND document_checksum = ?",
            [doc["document_id"], doc["document_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO downloaded_documents (
                document_id, source_url, http_status, mime_type, file_extension,
                file_size_bytes, raw_path, document_checksum, downloaded_at, ingestion_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                doc["document_id"], doc["source_url"], doc["http_status"], doc["mime_type"],
                doc.get("file_extension"), doc["file_size_bytes"], doc["raw_path"],
                doc["document_checksum"], doc["downloaded_at"], doc["ingestion_run_id"]
            ]
        )
        return True

    def save_extracted_document(self, doc: dict) -> bool:
        existing = self.connection.execute(
            """
            SELECT COUNT(*) FROM extracted_documents
            WHERE document_id = ? AND document_checksum = ? AND normalized_text_checksum = ?
            """,
            [doc["document_id"], doc["document_checksum"], doc["normalized_text_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO extracted_documents (
                document_id, document_checksum, extraction_method, extracted_text,
                text_length, page_count, language, normalized_text_checksum,
                extraction_quality, extracted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                doc["document_id"], doc["document_checksum"], doc["extraction_method"],
                doc["text"], doc["text_length"], doc.get("page_count"), doc.get("language"),
                doc["normalized_text_checksum"], doc["extraction_quality"], doc["extracted_at"]
            ]
        )
        return True

    def save_duplicate_link(self, link: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO document_duplicate_links (
                canonical_document_id, duplicate_document_id, duplicate_type, similarity, detected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                link["canonical_document_id"], link["duplicate_document_id"],
                link["duplicate_type"], link["similarity"], datetime.now(timezone.utc)
            ]
        )

    def save_evidence_claim(self, claim: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO evidence_claims (
                claim_id, document_id, cvm_code, ticker, claim_type, subject, predicate,
                object_text, numeric_value, unit, currency, effective_date, horizon_end,
                source_page, source_start, source_end, source_excerpt, extraction_method,
                confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                claim["claim_id"], claim["document_id"], claim["cvm_code"], claim.get("ticker"),
                claim["claim_type"], claim["subject"], claim["predicate"], claim["object_text"],
                claim.get("numeric_value"), claim.get("unit"), claim.get("currency"),
                claim.get("effective_date"), claim.get("horizon_end"), claim.get("source_page"),
                claim.get("source_start"), claim.get("source_end"), claim["source_excerpt"],
                claim["extraction_method"], claim["confidence"], claim["created_at"]
            ]
        )

    def count_downloaded_documents(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM downloaded_documents").fetchone()
        return res[0] if res else 0

    def count_extracted_documents(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM extracted_documents").fetchone()
        return res[0] if res else 0

    def count_evidence_claims(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()
        return res[0] if res else 0

    def close(self) -> None:
        self.connection.close()
