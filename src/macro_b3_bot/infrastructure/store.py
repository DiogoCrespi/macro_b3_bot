from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, date
from typing import Optional
import duckdb

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
                category_score DOUBLE DEFAULT 0.0,
                recency_score DOUBLE DEFAULT 0.0,
                ticker_mapping_score DOUBLE DEFAULT 0.0,
                liquidity_score DOUBLE DEFAULT 0.0,
                material_terms_score DOUBLE DEFAULT 0.0,
                materiality_score DOUBLE,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error VARCHAR,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
        """)
        for col in ["category_score", "recency_score", "ticker_mapping_score", "liquidity_score", "material_terms_score"]:
            try:
                self.connection.execute(f"ALTER TABLE ipe_processing_queue ADD COLUMN {col} DOUBLE DEFAULT 0.0;")
            except Exception:
                pass
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
        # Event Candidates (Sprint 2C-C)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS event_candidates (
                event_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                effective_date DATE,
                claim_ids VARCHAR NOT NULL,
                evidence_count INTEGER NOT NULL,
                novelty_score DOUBLE NOT NULL,
                materiality_score DOUBLE NOT NULL,
                persistence_score DOUBLE NOT NULL,
                quantitative_impact VARCHAR,
                invalidators VARCHAR,
                publication_timestamp TIMESTAMP,
                status VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            );
        """)
        
        # Alter table para bases existentes
        try:
            self.connection.execute("ALTER TABLE event_candidates ADD COLUMN publication_timestamp TIMESTAMP;")
        except Exception:
            pass
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
        # --- Sprint 3B Tables ---
        # Market Prices (OHLCV imutável com versionamento por source+checksum)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS market_prices (
                ticker VARCHAR NOT NULL,
                trading_date DATE NOT NULL,
                open DECIMAL(28, 8),
                high DECIMAL(28, 8),
                low DECIMAL(28, 8),
                close DECIMAL(28, 8) NOT NULL,
                adjusted_close DECIMAL(28, 8),
                volume DECIMAL(28, 4),
                source VARCHAR NOT NULL,
                collected_at TIMESTAMP NOT NULL,
                record_checksum VARCHAR NOT NULL,
                PRIMARY KEY (ticker, trading_date, source, record_checksum)
            );
        """)
        # Event Market Mappings (cvm_code → primary_ticker)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS event_market_mappings (
                event_id VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                primary_ticker VARCHAR NOT NULL,
                related_tickers VARCHAR NOT NULL DEFAULT '[]',
                market_symbol VARCHAR NOT NULL,
                asset_class VARCHAR NOT NULL DEFAULT 'STOCK',
                mapping_confidence DOUBLE NOT NULL,
                mapping_source VARCHAR NOT NULL,
                validated BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, primary_ticker)
            );
        """)
        # Effective Market Events (sessão de publicação + datas efetivas B3)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS effective_market_events (
                event_id VARCHAR PRIMARY KEY,
                publication_timestamp TIMESTAMP NOT NULL,
                publication_session VARCHAR NOT NULL,
                previous_trading_date DATE,
                effective_trading_date DATE NOT NULL,
                first_full_trading_date DATE NOT NULL,
                calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Event Market Outcomes (resultados do event study)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS event_market_outcomes (
                event_id VARCHAR NOT NULL,
                ticker VARCHAR NOT NULL,
                publication_timestamp TIMESTAMP NOT NULL,
                effective_trading_date DATE NOT NULL,
                publication_session VARCHAR NOT NULL,
                prior_close DOUBLE,
                raw_return_1d DOUBLE,
                raw_return_5d DOUBLE,
                raw_return_20d DOUBLE,
                raw_return_60d DOUBLE,
                car_1d DOUBLE,
                car_5d DOUBLE,
                car_20d DOUBLE,
                car_60d DOUBLE,
                pre_event_car_5d DOUBLE,
                event_window_car DOUBLE,
                beta DOUBLE,
                historical_volatility DOUBLE,
                volume_zscore DOUBLE,
                bootstrap_pvalue_1d DOUBLE,
                bootstrap_pvalue_5d DOUBLE,
                bootstrap_pvalue_20d DOUBLE,
                bh_adjusted_pvalue_5d DOUBLE,
                bh_threshold_5d DOUBLE,
                outcome_label VARCHAR NOT NULL,
                calculated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (event_id, ticker)
            );
        """)
        
        # Alter table para bases existentes
        for col in ["bh_adjusted_pvalue_5d", "bh_threshold_5d"]:
            try:
                self.connection.execute(f"ALTER TABLE event_market_outcomes ADD COLUMN {col} DOUBLE;")
            except Exception:
                pass

        # ── Sprint 4A: Global Macro Engine ────────────────────────────────────
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_releases (
                release_id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                series_code VARCHAR NOT NULL,
                indicator VARCHAR NOT NULL,
                geography VARCHAR NOT NULL,          -- JSON array
                frequency VARCHAR NOT NULL,
                unit VARCHAR NOT NULL,

                reference_date DATE NOT NULL,
                published_at TIMESTAMP,
                available_at TIMESTAMP NOT NULL,
                collected_at TIMESTAMP,
                vintage_date DATE,
                realtime_start DATE,
                realtime_end DATE,
                availability_precision VARCHAR NOT NULL DEFAULT 'EXACT',
                revision_number INTEGER NOT NULL DEFAULT 0,
                is_initial_release BOOLEAN NOT NULL DEFAULT TRUE,

                actual_value DECIMAL(28, 10) NOT NULL,
                previous_value DECIMAL(28, 10),
                revised_previous_value DECIMAL(28, 10),
                consensus_value DECIMAL(28, 10),

                raw_checksum VARCHAR NOT NULL,
                record_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL,

                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_data_vintages (
                vintage_id VARCHAR PRIMARY KEY,
                series_code VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                reference_date DATE NOT NULL,
                vintage_date DATE NOT NULL,
                value DECIMAL(28, 10) NOT NULL,
                is_latest BOOLEAN NOT NULL DEFAULT FALSE,
                ingestion_run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_event_candidates (
                event_id VARCHAR PRIMARY KEY,
                event_type VARCHAR NOT NULL,
                indicator VARCHAR NOT NULL,
                geography VARCHAR NOT NULL,           -- JSON array
                affected_variables VARCHAR NOT NULL,  -- JSON array

                reference_date DATE NOT NULL,
                detected_at TIMESTAMP NOT NULL,
                horizon_months INTEGER NOT NULL,

                actual_value DECIMAL(28, 10),
                expected_value DECIMAL(28, 10),
                surprise_value DECIMAL(28, 10),

                surprise_score DOUBLE NOT NULL,
                novelty_score DOUBLE NOT NULL,
                persistence_score DOUBLE NOT NULL,
                regime_shift_score DOUBLE NOT NULL,
                data_quality_score DOUBLE NOT NULL,

                direction VARCHAR NOT NULL,
                current_regime VARCHAR NOT NULL,

                evidence_ids VARCHAR NOT NULL,        -- JSON array of release_ids
                status VARCHAR NOT NULL DEFAULT 'PENDING',

                score_breakdown VARCHAR,              -- JSON dict

                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_event_evidence_links (
                event_id VARCHAR NOT NULL,
                release_id VARCHAR NOT NULL,
                link_type VARCHAR NOT NULL DEFAULT 'PRIMARY',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, release_id)
            );
        """)

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS macro_regime_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                snapshot_date DATE NOT NULL,
                captured_at TIMESTAMP NOT NULL,

                growth_direction VARCHAR NOT NULL,
                inflation_direction VARCHAR NOT NULL,
                liquidity_stance VARCHAR NOT NULL,
                oil_regime VARCHAR NOT NULL,
                enso_phase VARCHAR NOT NULL,

                regime_label VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,

                evidence_release_ids VARCHAR NOT NULL,  -- JSON array
                ingestion_run_id VARCHAR NOT NULL,

                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            "SELECT COUNT(*) FROM macro_observations WHERE source = ? AND series_code = ? AND reference_date = ? AND value = ?",
            [obs["source"], obs["series_code"], obs["reference_date"], obs["value"]]
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

    # ── Sprint 4A: Global Macro Release persistence ──────────────────────────

    def save_macro_release(self, rel: dict) -> bool:
        """
        Idempotent upsert of a MacroRelease.
        Uses record_checksum for deduplication (same series/date/value/vintage = same record).
        Returns True if a new record was inserted, False if it already existed.
        """
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM macro_releases WHERE record_checksum = ?",
            [rel["record_checksum"]]
        ).fetchone()[0]

        if existing > 0:
            return False

        geography = json.dumps(rel.get("geography", []))
        self.connection.execute(
            """
            INSERT INTO macro_releases (
                release_id, source, series_code, indicator, geography, frequency, unit,
                reference_date, published_at, available_at, collected_at, vintage_date,
                realtime_start, realtime_end, availability_precision, revision_number, is_initial_release,
                actual_value, previous_value, revised_previous_value, consensus_value,
                raw_checksum, record_checksum, ingestion_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rel["release_id"], rel["source"], rel["series_code"], rel["indicator"],
                geography, rel["frequency"], rel["unit"],
                rel["reference_date"], rel.get("published_at"), rel["available_at"],
                rel.get("collected_at"), rel.get("vintage_date"), rel.get("realtime_start"), rel.get("realtime_end"),
                rel.get("availability_precision", "EXACT"), rel.get("revision_number", 0), rel.get("is_initial_release", True),
                rel["actual_value"], rel.get("previous_value"), rel.get("revised_previous_value"),
                rel.get("consensus_value"),
                rel["raw_checksum"], rel["record_checksum"], rel["ingestion_run_id"],
                datetime.now(timezone.utc),
            ]
        )
        return True

    def save_macro_vintage(self, vint: dict) -> bool:
        """Idempotent upsert of a MacroDataVintage."""
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM macro_data_vintages WHERE vintage_id = ?",
            [vint["vintage_id"]]
        ).fetchone()[0]
        if existing > 0:
            return False

        self.connection.execute(
            """
            INSERT INTO macro_data_vintages (
                vintage_id, series_code, source, reference_date, vintage_date,
                value, is_latest, ingestion_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                vint["vintage_id"], vint["series_code"], vint["source"],
                vint["reference_date"], vint["vintage_date"],
                vint["value"], vint.get("is_latest", False),
                vint["ingestion_run_id"], datetime.now(timezone.utc),
            ]
        )
        return True

    def save_macro_event_candidate(self, evt: dict) -> bool:
        """Idempotent upsert of a MacroEventCandidate."""
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM macro_event_candidates WHERE event_id = ?",
            [evt["event_id"]]
        ).fetchone()[0]
        if existing > 0:
            return False

        import json as _json
        geography = _json.dumps(evt.get("geography", []))
        affected_variables = _json.dumps(evt.get("affected_variables", []))
        evidence_ids = _json.dumps(evt.get("evidence_ids", []))
        score_breakdown = _json.dumps(evt.get("score_breakdown", {}))

        self.connection.execute(
            """
            INSERT INTO macro_event_candidates (
                event_id, event_type, indicator, geography, affected_variables,
                reference_date, detected_at, horizon_months,
                actual_value, expected_value, surprise_value,
                surprise_score, novelty_score, persistence_score, regime_shift_score, data_quality_score,
                direction, current_regime, evidence_ids, status, score_breakdown, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                evt["event_id"], evt["event_type"], evt["indicator"], geography, affected_variables,
                evt["reference_date"], evt["detected_at"], evt["horizon_months"],
                evt.get("actual_value"), evt.get("expected_value"), evt.get("surprise_value"),
                evt["surprise_score"], evt["novelty_score"], evt["persistence_score"],
                evt["regime_shift_score"], evt["data_quality_score"],
                evt["direction"], evt["current_regime"], evidence_ids,
                evt.get("status", "PENDING"), score_breakdown, datetime.now(timezone.utc),
            ]
        )
        # Link evidence
        for rid in evt.get("evidence_ids", []):
            try:
                self.connection.execute(
                    "INSERT INTO macro_event_evidence_links (event_id, release_id) VALUES (?, ?)",
                    [evt["event_id"], rid]
                )
            except Exception:
                pass
        return True

    def update_macro_event_status(self, event_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE macro_event_candidates SET status = ? WHERE event_id = ?",
            [status, event_id]
        )

    def save_macro_regime_snapshot(self, snap: dict) -> bool:
        """Idempotent upsert of a MacroRegimeSnapshot."""
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM macro_regime_snapshots WHERE snapshot_id = ?",
            [snap["snapshot_id"]]
        ).fetchone()[0]
        if existing > 0:
            return False

        import json as _json
        evidence_release_ids = _json.dumps(snap.get("evidence_release_ids", []))

        self.connection.execute(
            """
            INSERT INTO macro_regime_snapshots (
                snapshot_id, snapshot_date, captured_at,
                growth_direction, inflation_direction, liquidity_stance, oil_regime, enso_phase,
                regime_label, confidence, evidence_release_ids, ingestion_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snap["snapshot_id"], snap["snapshot_date"], snap["captured_at"],
                snap["growth_direction"], snap["inflation_direction"],
                snap["liquidity_stance"], snap["oil_regime"], snap["enso_phase"],
                snap["regime_label"], snap["confidence"],
                evidence_release_ids, snap["ingestion_run_id"], datetime.now(timezone.utc),
            ]
        )
        return True

    def get_macro_releases_for_series(
        self, source: str, series_code: str, limit: int = 500
    ) -> list[dict]:
        """Return recent releases ordered by reference_date DESC."""
        rows = self.connection.execute(
            """
            SELECT release_id, reference_date, published_at, available_at,
                   actual_value, previous_value, consensus_value
            FROM macro_releases
            WHERE source = ? AND series_code = ?
            ORDER BY reference_date DESC
            LIMIT ?
            """,
            [source, series_code, limit]
        ).fetchall()
        cols = ["release_id", "reference_date", "published_at", "available_at",
                "actual_value", "previous_value", "consensus_value"]
        return [dict(zip(cols, r)) for r in rows]

    def get_macro_event_candidates(self, status: Optional[str] = None) -> list[dict]:
        """Return macro event candidates, optionally filtered by status."""
        if status:
            rows = self.connection.execute(
                "SELECT * FROM macro_event_candidates WHERE status = ? ORDER BY detected_at DESC",
                [status]
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM macro_event_candidates ORDER BY detected_at DESC"
            ).fetchall()
        cols = [d[0] for d in self.connection.description]
        return [dict(zip(cols, r)) for r in rows]

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
                document_id, status, priority_score, category_score, recency_score,
                ticker_mapping_score, liquidity_score, material_terms_score,
                materiality_score, attempts, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                state["document_id"], state["status"], state["priority_score"],
                state.get("category_score", 0.0), state.get("recency_score", 0.0),
                state.get("ticker_mapping_score", 0.0), state.get("liquidity_score", 0.0),
                state.get("material_terms_score", 0.0), state.get("materiality_score"),
                state.get("attempts", 0), state.get("last_error"),
                datetime.now(timezone.utc), datetime.now(timezone.utc)
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

    def save_event_candidate(self, candidate: dict) -> None:
        import json
        from datetime import date, time, datetime
        pub_ts = candidate.get("publication_timestamp")
        if pub_ts is None and candidate.get("effective_date") is not None:
            eff = candidate.get("effective_date")
            if isinstance(eff, str):
                eff = date.fromisoformat(eff)
            pub_ts = datetime.combine(eff, time(0, 0))
        if pub_ts is None:
            pub_ts = datetime.now(timezone.utc)

        self.connection.execute(
            """
            INSERT OR REPLACE INTO event_candidates (
                event_id, ticker, cvm_code, event_type, title, effective_date,
                claim_ids, evidence_count, novelty_score, materiality_score,
                persistence_score, quantitative_impact, invalidators, publication_timestamp, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                candidate["event_id"], candidate["ticker"], candidate["cvm_code"],
                candidate["event_type"], candidate["title"], candidate.get("effective_date"),
                json.dumps(candidate.get("claim_ids", [])), candidate.get("evidence_count", 1),
                candidate["novelty_score"], candidate["materiality_score"],
                candidate.get("persistence_score", 0.8),
                json.dumps(candidate.get("quantitative_impact", {})),
                json.dumps(candidate.get("invalidators", [])),
                pub_ts,
                candidate["status"], datetime.now(timezone.utc)
            ]
        )

    def count_event_candidates(self) -> int:
        res = self.connection.execute("SELECT COUNT(*) FROM event_candidates").fetchone()
        return res[0] if res else 0

    def save_event_market_mapping(self, mapping: dict) -> None:
        import json
        self.connection.execute(
            """
            INSERT OR REPLACE INTO event_market_mappings (
                event_id, cvm_code, primary_ticker, related_tickers,
                market_symbol, asset_class, mapping_confidence, mapping_source, validated, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                mapping["event_id"], mapping["cvm_code"], mapping["primary_ticker"],
                json.dumps(mapping.get("related_tickers", [])),
                mapping["market_symbol"], mapping.get("asset_class", "STOCK"),
                mapping["mapping_confidence"], mapping["mapping_source"],
                mapping.get("validated", False), datetime.now(timezone.utc)
            ]
        )

    def get_event_market_mapping(self, event_id: str) -> Optional[dict]:
        row = self.connection.execute(
            """
            SELECT event_id, cvm_code, primary_ticker, related_tickers,
                   market_symbol, asset_class, mapping_confidence, mapping_source, validated
            FROM event_market_mappings WHERE event_id = ?
            """,
            [event_id]
        ).fetchone()
        if not row:
            return None
        import json
        return {
            "event_id": row[0],
            "cvm_code": row[1],
            "primary_ticker": row[2],
            "related_tickers": json.loads(row[3]),
            "market_symbol": row[4],
            "asset_class": row[5],
            "mapping_confidence": row[6],
            "mapping_source": row[7],
            "validated": bool(row[8]),
        }

    def save_market_price(self, price: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO market_prices (
                ticker, trading_date, open, high, low, close, adjusted_close, volume, source, collected_at, record_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                price["ticker"], price["trading_date"],
                price.get("open"), price.get("high"), price.get("low"),
                price["close"], price.get("adjusted_close"), price.get("volume"),
                price["source"], price.get("collected_at", datetime.now(timezone.utc)),
                price.get("record_checksum", "")
            ]
        )

    def get_market_prices(self, ticker: str, start_date: date, end_date: date) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT ticker, trading_date, open, high, low, close, adjusted_close, volume, source, collected_at, record_checksum
            FROM market_prices
            WHERE ticker = ? AND trading_date >= ? AND trading_date <= ?
            ORDER BY trading_date ASC
            """,
            [ticker, start_date, end_date]
        ).fetchall()
        return [
            {
                "ticker": r[0],
                "trading_date": r[1] if isinstance(r[1], date) else date.fromisoformat(str(r[1])[:10]),
                "open": r[2],
                "high": r[3],
                "low": r[4],
                "close": r[5],
                "adjusted_close": r[6],
                "volume": r[7],
                "source": r[8],
                "collected_at": r[9],
                "record_checksum": r[10]
            }
            for r in rows
        ]

    def save_effective_market_event(self, event: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO effective_market_events (
                event_id, publication_timestamp, publication_session,
                previous_trading_date, effective_trading_date, first_full_trading_date, calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event["event_id"], event["publication_timestamp"], event["publication_session"],
                event.get("previous_trading_date"), event["effective_trading_date"],
                event["first_full_trading_date"], datetime.now(timezone.utc)
            ]
        )

    def get_effective_market_event(self, event_id: str) -> Optional[dict]:
        row = self.connection.execute(
            """
            SELECT event_id, publication_timestamp, publication_session,
                   previous_trading_date, effective_trading_date, first_full_trading_date
            FROM effective_market_events WHERE event_id = ?
            """,
            [event_id]
        ).fetchone()
        if not row:
            return None
        return {
            "event_id": row[0],
            "publication_timestamp": row[1],
            "publication_session": row[2],
            "previous_trading_date": row[3] if isinstance(row[3], date) or row[3] is None else date.fromisoformat(str(row[3])[:10]),
            "effective_trading_date": row[4] if isinstance(row[4], date) else date.fromisoformat(str(row[4])[:10]),
            "first_full_trading_date": row[5] if isinstance(row[5], date) else date.fromisoformat(str(row[5])[:10]),
        }

    def save_event_market_outcome(self, outcome: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO event_market_outcomes (
                event_id, ticker, publication_timestamp, effective_trading_date, publication_session,
                prior_close, raw_return_1d, raw_return_5d, raw_return_20d, raw_return_60d,
                car_1d, car_5d, car_20d, car_60d, pre_event_car_5d, event_window_car,
                beta, historical_volatility, volume_zscore,
                bootstrap_pvalue_1d, bootstrap_pvalue_5d, bootstrap_pvalue_20d, outcome_label, calculated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                outcome["event_id"], outcome["ticker"], outcome["publication_timestamp"],
                outcome["effective_trading_date"], outcome["publication_session"],
                outcome.get("prior_close"), outcome.get("raw_return_1d"), outcome.get("raw_return_5d"),
                outcome.get("raw_return_20d"), outcome.get("raw_return_60d"),
                outcome.get("car_1d"), outcome.get("car_5d"), outcome.get("car_20d"), outcome.get("car_60d"),
                outcome.get("pre_event_car_5d"), outcome.get("event_window_car"),
                outcome.get("beta"), outcome.get("historical_volatility"), outcome.get("volume_zscore"),
                outcome.get("bootstrap_pvalue_1d"), outcome.get("bootstrap_pvalue_5d"),
                outcome.get("bootstrap_pvalue_20d"), outcome["outcome_label"], datetime.now(timezone.utc)
            ]
        )

    def get_event_market_outcome(self, event_id: str, ticker: str) -> Optional[dict]:
        row = self.connection.execute(
            """
            SELECT event_id, ticker, publication_timestamp, effective_trading_date, publication_session,
                   prior_close, raw_return_1d, raw_return_5d, raw_return_20d, raw_return_60d,
                   car_1d, car_5d, car_20d, car_60d, pre_event_car_5d, event_window_car,
                   beta, historical_volatility, volume_zscore,
                   bootstrap_pvalue_1d, bootstrap_pvalue_5d, bootstrap_pvalue_20d, outcome_label
            FROM event_market_outcomes WHERE event_id = ? AND ticker = ?
            """,
            [event_id, ticker]
        ).fetchone()
        if not row:
            return None
        return {
            "event_id": row[0],
            "ticker": row[1],
            "publication_timestamp": row[2],
            "effective_trading_date": row[3] if isinstance(row[3], date) else date.fromisoformat(str(row[3])[:10]),
            "publication_session": row[4],
            "prior_close": row[5],
            "raw_return_1d": row[6],
            "raw_return_5d": row[7],
            "raw_return_20d": row[8],
            "raw_return_60d": row[9],
            "car_1d": row[10],
            "car_5d": row[11],
            "car_20d": row[12],
            "car_60d": row[13],
            "pre_event_car_5d": row[14],
            "event_window_car": row[15],
            "beta": row[16],
            "historical_volatility": row[17],
            "volume_zscore": row[18],
            "bootstrap_pvalue_1d": row[19],
            "bootstrap_pvalue_5d": row[20],
            "bootstrap_pvalue_20d": row[21],
            "outcome_label": row[22]
        }

    def close(self) -> None:
        self.connection.close()
