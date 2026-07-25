from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from datetime import datetime, timezone, date
from typing import Optional, Any
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
                legal_name VARCHAR,
                valid_from DATE,
                valid_to DATE,
                review_status VARCHAR NOT NULL DEFAULT 'UNREVIEWED',
                evidence_id VARCHAR,
                mapping_version VARCHAR,
                PRIMARY KEY (ticker, cnpj)
            );
        """)
        for col, kind in {
            "legal_name": "VARCHAR", "valid_from": "DATE", "valid_to": "DATE",
            "review_status": "VARCHAR DEFAULT 'UNREVIEWED'", "evidence_id": "VARCHAR",
            "mapping_version": "VARCHAR",
        }.items():
            try:
                self.connection.execute(f"ALTER TABLE company_ticker_map ADD COLUMN {col} {kind};")
            except Exception:
                pass
        # CVM Documents (ITR / DFP)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS cvm_documents (
                document_id VARCHAR PRIMARY KEY,
                document_type VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                cnpj VARCHAR NOT NULL,
                reference_date DATE NOT NULL,
                received_at TIMESTAMP NOT NULL,
                filing_available_at TIMESTAMP,
                resource_last_modified_at TIMESTAMP,
                collected_at TIMESTAMP,
                availability_precision VARCHAR DEFAULT 'UNKNOWN',
                version INTEGER NOT NULL,
                raw_zip_checksum VARCHAR NOT NULL,
                ingestion_run_id VARCHAR NOT NULL,
                availability_basis VARCHAR,
                source_url VARCHAR
            );
        """)
        for col, kind in {
            "availability_basis": "VARCHAR",
            "source_url": "VARCHAR",
            "filing_available_at": "TIMESTAMP",
            "resource_last_modified_at": "TIMESTAMP",
            "collected_at": "TIMESTAMP",
            "availability_precision": "VARCHAR DEFAULT 'UNKNOWN'",
        }.items():
            try:
                self.connection.execute(f"ALTER TABLE cvm_documents ADD COLUMN {col} {kind};")
            except Exception:
                pass
        self.connection.execute(
            """
            UPDATE cvm_documents
            SET resource_last_modified_at = COALESCE(resource_last_modified_at,received_at),
                availability_precision = 'CONSERVATIVE_RESOURCE_DATE'
            WHERE availability_basis = 'RESOURCE_LAST_MODIFIED'
              AND (resource_last_modified_at IS NULL
                   OR availability_precision IS NULL
                   OR availability_precision = 'UNKNOWN')
            """
        )
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
        # Raw MiroFish Reports
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS raw_mirofish_reports (
                report_id VARCHAR PRIMARY KEY,
                simulation_id VARCHAR,
                project_id VARCHAR,
                content_checksum VARCHAR NOT NULL,
                byte_size BIGINT NOT NULL,
                mime_type VARCHAR NOT NULL,
                retrieved_at TIMESTAMP NOT NULL,
                source_endpoint VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL
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
                realtime_start DATE,
                realtime_end DATE,
                available_at TIMESTAMP NOT NULL,
                value DECIMAL(28, 10) NOT NULL,
                revision_number INTEGER NOT NULL DEFAULT 0,
                is_initial_release BOOLEAN NOT NULL DEFAULT TRUE,
                is_latest BOOLEAN NOT NULL DEFAULT FALSE,
                record_checksum VARCHAR NOT NULL,
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
                source VARCHAR,
                series_code VARCHAR,
                ingestion_run_id VARCHAR,

                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        for col in ["source", "series_code", "ingestion_run_id"]:
            try:
                self.connection.execute(f"ALTER TABLE macro_event_candidates ADD COLUMN {col} VARCHAR;")
            except Exception:
                pass

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

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS sector_impact_candidates (
                candidate_id VARCHAR PRIMARY KEY,
                event_id VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                sector VARCHAR NOT NULL,
                subsector VARCHAR,
                direction VARCHAR NOT NULL,
                impact_score DOUBLE NOT NULL,
                confidence DOUBLE NOT NULL,
                horizon_months INTEGER NOT NULL,
                causal_paths VARCHAR NOT NULL,
                direct_effects VARCHAR NOT NULL,
                second_order_effects VARCHAR NOT NULL,
                positive_paths_count INTEGER NOT NULL,
                negative_paths_count INTEGER NOT NULL,
                conflict_detected BOOLEAN NOT NULL,
                invalidators VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                detected_at TIMESTAMP NOT NULL,
                causal_root VARCHAR,
                event_strength DOUBLE,
                horizon_days INTEGER,
                evidence_status VARCHAR,
                event_available_at TIMESTAMP,
                as_of_timestamp TIMESTAMP,
                run_id VARCHAR,
                source_event_run_id VARCHAR,
                graph_version VARCHAR,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        for col, kind in {
            "causal_root": "VARCHAR", "event_strength": "DOUBLE", "horizon_days": "INTEGER",
            "evidence_status": "VARCHAR", "event_available_at": "TIMESTAMP",
            "as_of_timestamp": "TIMESTAMP", "run_id": "VARCHAR",
            "source_event_run_id": "VARCHAR", "graph_version": "VARCHAR",
        }.items():
            try:
                self.connection.execute(f"ALTER TABLE sector_impact_candidates ADD COLUMN {col} {kind};")
            except Exception:
                pass

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS sector_state_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                sector VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                net_impact DOUBLE NOT NULL,
                bullish_impact DOUBLE NOT NULL,
                bearish_impact DOUBLE NOT NULL,
                conflict_ratio DOUBLE NOT NULL,
                supporting_event_ids VARCHAR NOT NULL,
                opposing_event_ids VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                graph_version VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS company_exposure_snapshots (
                exposure_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                sector VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                reference_date DATE NOT NULL,
                exposure_version VARCHAR NOT NULL,
                exposure_payload VARCHAR NOT NULL,
                field_evidence VARCHAR NOT NULL,
                missing_fields VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                evidence_quality_score DOUBLE,
                completeness_score DOUBLE,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            );
        """)
        for col in ("evidence_quality_score", "completeness_score"):
            try:
                self.connection.execute(
                    f"ALTER TABLE company_exposure_snapshots ADD COLUMN {col} DOUBLE;"
                )
            except Exception:
                pass
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS company_exposure_overrides (
                override_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                field_name VARCHAR NOT NULL,
                previous_value VARCHAR,
                new_value VARCHAR NOT NULL,
                rationale VARCHAR NOT NULL,
                evidence_ids VARCHAR NOT NULL,
                approved_by VARCHAR NOT NULL,
                approved_at TIMESTAMP NOT NULL,
                methodology_version VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS company_impact_candidates (
                candidate_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                sector_snapshot_id VARCHAR NOT NULL,
                company_exposure_id VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                impact_payload VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                conflict_ratio DOUBLE NOT NULL,
                missing_exposures VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS financial_baseline_snapshots (
                baseline_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                latest_quarter DATE NOT NULL,
                baseline_payload VARCHAR NOT NULL,
                field_evidence VARCHAR NOT NULL,
                missing_fields VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                methodology_version VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS economic_shock_scenarios (
                scenario_id VARCHAR PRIMARY KEY,
                factor VARCHAR NOT NULL,
                scenario_case VARCHAR NOT NULL,
                magnitude DOUBLE NOT NULL,
                unit VARCHAR NOT NULL,
                horizon_years DOUBLE NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                scenario_payload VARCHAR NOT NULL,
                methodology_version VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS financial_scenario_outcomes (
                outcome_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                scenario_case VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                baseline_id VARCHAR NOT NULL,
                company_impact_candidate_id VARCHAR NOT NULL,
                outcome_payload VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS financial_bridge_calibrations (
                calibration_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                bridge VARCHAR NOT NULL,
                calibration_payload VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS normalized_cash_flow_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                snapshot_payload VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS causal_conflict_diagnostics (
                diagnostic_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                factor VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                diagnostic_payload VARCHAR NOT NULL,
                classification VARCHAR NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS valuation_readiness_assessments (
                assessment_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                assessment_payload VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                valuation_eligible BOOLEAN NOT NULL,
                run_id VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots_pit (
                market_snapshot_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                assessment_as_of TIMESTAMP,
                price_as_of TIMESTAMP,
                price_available_at TIMESTAMP,
                share_count_as_of TIMESTAMP,
                share_count_available_at TIMESTAMP,
                as_of_timestamp TIMESTAMP NOT NULL,
                available_at TIMESTAMP,
                price DOUBLE,
                share_count DOUBLE,
                share_count_basis VARCHAR,
                currency VARCHAR,
                source_id VARCHAR,
                market_data_version VARCHAR,
                security_type VARCHAR,
                equity_value_basis VARCHAR,
                price_source_file VARCHAR,
                price_source_checksum VARCHAR,
                price_layout_version VARCHAR,
                price_record_hash VARCHAR,
                share_document_id VARCHAR,
                share_document_version VARCHAR,
                share_document_checksum VARCHAR,
                share_section VARCHAR,
                isin VARCHAR,
                pit_assurance VARCHAR,
                market_capitalization DOUBLE,
                enterprise_value DOUBLE,
                pe_observed DOUBLE,
                snapshot_payload VARCHAR,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        for col, kind in {
            "isin": "VARCHAR",
            "pit_assurance": "VARCHAR",
            "market_capitalization": "DOUBLE",
            "enterprise_value": "DOUBLE",
            "pe_observed": "DOUBLE",
        }.items():
            try:
                self.connection.execute(f"ALTER TABLE market_snapshots_pit ADD COLUMN {col} {kind};")
            except Exception:
                pass
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS pit_security_mappings (
                mapping_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                cvm_code VARCHAR NOT NULL,
                cnpj VARCHAR NOT NULL,
                isin VARCHAR NOT NULL,
                security_type VARCHAR NOT NULL,
                valid_from TIMESTAMP NOT NULL,
                valid_to TIMESTAMP,
                mapping_source VARCHAR NOT NULL,
                mapping_available_at TIMESTAMP NOT NULL,
                mapping_checksum VARCHAR NOT NULL,
                mapping_payload VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_valuation_observations (
                observation_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                valuation_date DATE NOT NULL,
                market_snapshot_id VARCHAR NOT NULL,
                financial_baseline_id VARCHAR NOT NULL,
                methodology_version VARCHAR NOT NULL,
                observation_payload VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS research_decision_snapshots (
                decision_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                decision VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                confidence_tier VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                input_ids_json VARCHAR NOT NULL,
                methodology_version VARCHAR NOT NULL,
                execution_mode VARCHAR NOT NULL DEFAULT 'BLOCKED_MISSING_UPSTREAM_INPUT',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS research_timing_risk_snapshots (
                timing_risk_id VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                research_decision_id VARCHAR NOT NULL,
                timing_classification VARCHAR NOT NULL,
                risk_classification VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                input_ids_json VARCHAR NOT NULL,
                methodology_version VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_portfolio_policies (
                policy_id VARCHAR PRIMARY KEY,
                initial_capital DOUBLE NOT NULL,
                max_weight_per_asset DOUBLE NOT NULL,
                max_weight_per_sector DOUBLE NOT NULL,
                min_cash_weight DOUBLE NOT NULL,
                min_position_weight DOUBLE NOT NULL,
                b3_emoluments_pct DOUBLE NOT NULL,
                base_slippage_pct DOUBLE NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_replay_runs (
                replay_run_id VARCHAR PRIMARY KEY,
                start_date TIMESTAMP NOT NULL,
                end_date TIMESTAMP NOT NULL,
                initial_capital DOUBLE NOT NULL,
                portfolio_policy_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_allocation_events (
                allocation_event_id VARCHAR PRIMARY KEY,
                portfolio_id VARCHAR NOT NULL,
                ticker VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                research_decision_id VARCHAR NOT NULL,
                timing_risk_id VARCHAR NOT NULL,
                execution_session VARCHAR NOT NULL,
                execution_price DOUBLE,
                open_price DOUBLE,
                quote_record_id VARCHAR,
                isin VARCHAR,
                source_checksum VARCHAR,
                target_weight DOUBLE NOT NULL,
                executed_weight DOUBLE NOT NULL,
                quantity_simulated DOUBLE NOT NULL,
                gross_value DOUBLE NOT NULL,
                transaction_cost DOUBLE NOT NULL,
                slippage_cost DOUBLE NOT NULL,
                reason VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_portfolio_snapshots (
                portfolio_snapshot_id VARCHAR PRIMARY KEY,
                portfolio_id VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                cash_balance DOUBLE NOT NULL,
                positions_value DOUBLE NOT NULL,
                nav DOUBLE NOT NULL,
                daily_pnl DOUBLE NOT NULL,
                total_realized_pnl DOUBLE NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS paper_portfolio_performance (
                report_id VARCHAR PRIMARY KEY,
                replay_run_id VARCHAR NOT NULL,
                total_return_pct DOUBLE NOT NULL,
                annualized_return_pct DOUBLE NOT NULL,
                max_drawdown_pct DOUBLE NOT NULL,
                total_costs_brl DOUBLE NOT NULL,
                total_slippage_brl DOUBLE NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS scenario_seed_packages (
                seed_package_id VARCHAR PRIMARY KEY,
                as_of_timestamp TIMESTAMP NOT NULL,
                prompt_template_version VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS mirofish_simulation_runs (
                simulation_run_id VARCHAR PRIMARY KEY,
                seed_package_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                prompt_hash VARCHAR NOT NULL,
                input_checksum VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS scenario_hypotheses (
                hypothesis_id VARCHAR PRIMARY KEY,
                simulation_run_id VARCHAR NOT NULL,
                scenario_type VARCHAR NOT NULL,
                verification_status VARCHAR NOT NULL,
                confidence DOUBLE,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS scenario_hypothesis_reviews (
                review_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                simulation_run_id VARCHAR NOT NULL,
                reviewer_type VARCHAR NOT NULL,
                reviewer_id VARCHAR NOT NULL,
                review_decision VARCHAR NOT NULL,
                review_status VARCHAR NOT NULL,
                review_confidence DOUBLE,
                fact_review_hash VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS scenario_hypothesis_validations (
                validation_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                validation_status VARCHAR NOT NULL,
                validator_type VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS scenario_hypothesis_bindings (
                binding_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                binding_status VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS scenario_sets (
                scenario_set_id VARCHAR PRIMARY KEY,
                event_id VARCHAR NOT NULL,
                as_of_timestamp TIMESTAMP NOT NULL,
                canonical_payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            self.connection.execute("ALTER TABLE research_decision_snapshots ADD COLUMN execution_mode VARCHAR DEFAULT 'BLOCKED_MISSING_UPSTREAM_INPUT'")
        except Exception:
            pass
        # Older local databases created confidence as NOT NULL.  Structured
        # MiroFish reports are allowed to omit confidence; preserve that
        # absence as SQL NULL rather than inventing a numeric value.
        try:
            self.connection.execute("ALTER TABLE scenario_hypotheses ALTER COLUMN confidence DROP NOT NULL")
        except Exception:
            pass
        for col_def in (
            "ALTER TABLE paper_allocation_events ADD COLUMN open_price DOUBLE",
            "ALTER TABLE paper_allocation_events ADD COLUMN quote_record_id VARCHAR",
            "ALTER TABLE paper_allocation_events ADD COLUMN isin VARCHAR",
            "ALTER TABLE paper_allocation_events ADD COLUMN source_checksum VARCHAR",
        ):
            try:
                self.connection.execute(col_def)
            except Exception:
                pass
        for col, kind in {
            "assessment_as_of": "TIMESTAMP",
            "price_as_of": "TIMESTAMP",
            "price_available_at": "TIMESTAMP",
            "share_count_as_of": "TIMESTAMP",
            "share_count_available_at": "TIMESTAMP",
            "price_source_file": "VARCHAR",
            "price_source_checksum": "VARCHAR",
            "price_layout_version": "VARCHAR",
            "price_record_hash": "VARCHAR",
            "share_document_id": "VARCHAR",
            "share_document_version": "VARCHAR",
            "share_document_checksum": "VARCHAR",
            "share_section": "VARCHAR",
            "isin": "VARCHAR",
            "pit_assurance": "VARCHAR",
            "market_capitalization": "DOUBLE",
            "enterprise_value": "DOUBLE",
            "pe_observed": "DOUBLE",
        }.items():
            try:
                self.connection.execute(
                    f"ALTER TABLE market_snapshots_pit ADD COLUMN {col} {kind}"
                )
            except Exception:
                pass


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

    def get_macro_releases_pit(self, cutoff_dt: datetime) -> list[dict]:
        """
        Retorna todas as macro_releases disponíveis no momento do cutoff (available_at <= cutoff_dt).
        """
        rows = self.connection.execute(
            """
            SELECT release_id, source, series_code, indicator, geography, frequency, unit,
                   reference_date, published_at, available_at, collected_at, vintage_date,
                   realtime_start, realtime_end, availability_precision, revision_number, is_initial_release,
                   actual_value, previous_value, revised_previous_value, consensus_value,
                   raw_checksum, record_checksum, ingestion_run_id, created_at
            FROM macro_releases
            WHERE available_at <= ?
            ORDER BY available_at DESC, reference_date DESC
            """,
            [cutoff_dt]
        ).fetchall()
        cols = [
            "release_id", "source", "series_code", "indicator", "geography", "frequency", "unit",
            "reference_date", "published_at", "available_at", "collected_at", "vintage_date",
            "realtime_start", "realtime_end", "availability_precision", "revision_number", "is_initial_release",
            "actual_value", "previous_value", "revised_previous_value", "consensus_value",
            "raw_checksum", "record_checksum", "ingestion_run_id", "created_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_evidence_claims_pit(self, cutoff_dt: datetime) -> list[dict]:
        """
        Retorna todos os evidence_claims disponíveis no momento do cutoff (created_at <= cutoff_dt).
        """
        rows = self.connection.execute(
            """
            SELECT claim_id, document_id, cvm_code, ticker, claim_type, subject, predicate,
                   object_text, numeric_value, unit, currency, effective_date, horizon_end,
                   source_page, source_start, source_end, source_excerpt, extraction_method,
                   confidence, created_at
            FROM evidence_claims
            WHERE created_at <= ?
            ORDER BY created_at DESC
            """,
            [cutoff_dt]
        ).fetchall()
        cols = [
            "claim_id", "document_id", "cvm_code", "ticker", "claim_type", "subject", "predicate",
            "object_text", "numeric_value", "unit", "currency", "effective_date", "horizon_end",
            "source_page", "source_start", "source_end", "source_excerpt", "extraction_method",
            "confidence", "created_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_sector_state_snapshots_pit(self, cutoff_dt: datetime) -> list[dict]:
        """
        Retorna todos os sector_state_snapshots disponíveis no momento do cutoff (as_of_timestamp <= cutoff_dt).
        Prioriza o mais recente por setor.
        """
        rows = self.connection.execute(
            """
            WITH ranked_snapshots AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY sector
                           ORDER BY as_of_timestamp DESC, created_at DESC
                       ) as rn
                FROM sector_state_snapshots
                WHERE as_of_timestamp <= ?
            )
            SELECT snapshot_id, sector, as_of_timestamp, net_impact, bullish_impact,
                   bearish_impact, conflict_ratio, supporting_event_ids, opposing_event_ids,
                   confidence, status, run_id, graph_version, created_at
            FROM ranked_snapshots
            WHERE rn = 1
            ORDER BY sector ASC, as_of_timestamp DESC
            """,
            [cutoff_dt]
        ).fetchall()
        cols = [
            "snapshot_id", "sector", "as_of_timestamp", "net_impact", "bullish_impact",
            "bearish_impact", "conflict_ratio", "supporting_event_ids", "opposing_event_ids",
            "confidence", "status", "run_id", "graph_version", "created_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_macro_regime_snapshots_pit(self, cutoff_dt: datetime) -> list[dict]:
        """
        Retorna todos os macro_regime_snapshots disponíveis no momento do cutoff (snapshot_date <= cutoff_dt).
        Prioriza o mais recente.
        """
        rows = self.connection.execute(
            """
            WITH ranked_snapshots AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY 'global' -- Assumindo que o regime macro é "global" e não por setor
                           ORDER BY snapshot_date DESC, captured_at DESC
                       ) as rn
                FROM macro_regime_snapshots
                WHERE snapshot_date <= ?
            )
            SELECT snapshot_id, snapshot_date, captured_at, growth_direction, inflation_direction,
                   liquidity_stance, oil_regime, enso_phase, regime_label, confidence,
                   evidence_release_ids, ingestion_run_id, created_at
            FROM ranked_snapshots
            WHERE rn = 1
            ORDER BY snapshot_date DESC
            """,
            [cutoff_dt]
        ).fetchall()
        cols = [
            "snapshot_id", "snapshot_date", "captured_at", "growth_direction", "inflation_direction",
            "liquidity_stance", "oil_regime", "enso_phase", "regime_label", "confidence",
            "evidence_release_ids", "ingestion_run_id", "created_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_macro_event_candidates_pit(self, cutoff_dt: datetime) -> list[dict]:
        """
        Retorna todos os macro_event_candidates disponíveis no momento do cutoff (detected_at <= cutoff_dt).
        """
        rows = self.connection.execute(
            """
            SELECT event_id, event_type, indicator, geography, affected_variables,
                   reference_date, detected_at, horizon_months, actual_value, expected_value,
                   surprise_value, surprise_score, novelty_score, persistence_score,
                   regime_shift_score, data_quality_score, direction, current_regime,
                   evidence_ids, status, score_breakdown, source, series_code,
                   ingestion_run_id, created_at
            FROM macro_event_candidates
            WHERE detected_at <= ?
            ORDER BY detected_at DESC
            """,
            [cutoff_dt]
        ).fetchall()
        cols = [
            "event_id", "event_type", "indicator", "geography", "affected_variables",
            "reference_date", "detected_at", "horizon_months", "actual_value", "expected_value",
            "surprise_value", "surprise_score", "novelty_score", "persistence_score",
            "regime_shift_score", "data_quality_score", "direction", "current_regime",
            "evidence_ids", "status", "score_breakdown", "source", "series_code",
            "ingestion_run_id", "created_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_source_documents_pit(self, cutoff_dt: datetime) -> list[dict]:
        """
        Retorna todos os documentos da CVM (cvm_documents) e documentos baixados (downloaded_documents)
        disponíveis no momento do cutoff (filing_available_at/downloaded_at <= cutoff_dt).
        """
        cvm_docs_rows = self.connection.execute(
            """
            SELECT document_id, document_type, cvm_code, cnpj, reference_date,
                   received_at, filing_available_at as available_at, version, raw_zip_checksum as checksum,
                   ingestion_run_id, 'CVM' as source_type
            FROM cvm_documents
            WHERE filing_available_at <= ?
            ORDER BY filing_available_at DESC
            """,
            [cutoff_dt]
        ).fetchall()
        cvm_docs_cols = [
            "document_id", "document_type", "cvm_code", "cnpj", "reference_date",
            "received_at", "available_at", "version", "checksum",
            "ingestion_run_id", "source_type"
        ]
        cvm_docs = [dict(zip(cvm_docs_cols, r)) for r in cvm_docs_rows]

        downloaded_docs_rows = self.connection.execute(
            """
            SELECT document_id, source_url, http_status, mime_type, file_extension,
                   file_size_bytes, raw_path, document_checksum as checksum, downloaded_at as available_at,
                   ingestion_run_id, 'DOWNLOADED' as source_type
            FROM downloaded_documents
            WHERE downloaded_at <= ?
            ORDER BY downloaded_at DESC
            """,
            [cutoff_dt]
        ).fetchall()
        downloaded_docs_cols = [
            "document_id", "source_url", "http_status", "mime_type", "file_extension",
            "file_size_bytes", "raw_path", "checksum", "available_at",
            "ingestion_run_id", "source_type"
        ]
        downloaded_docs = [dict(zip(downloaded_docs_cols, r)) for r in downloaded_docs_rows]

        return cvm_docs + downloaded_docs

    def get_loader_diagnostics(self, cutoff_dt: datetime, event_id: str | None = None) -> dict[str, Any]:
        """
        Generates mandatory audit breakdown of data loaders for PIT seed generation.
        """
        iso_cutoff = cutoff_dt.isoformat()

        # 1. Macro releases
        scanned_macro = self.connection.execute("SELECT COUNT(*) FROM macro_releases").fetchone()[0]
        valid_id_macro = self.connection.execute("SELECT COUNT(*) FROM macro_releases WHERE release_id IS NOT NULL AND release_id != ''").fetchone()[0]
        pit_eligible_macro = self.connection.execute("SELECT COUNT(*) FROM macro_releases WHERE available_at <= ?", [cutoff_dt]).fetchone()[0]
        future_macro = self.connection.execute("SELECT COUNT(*) FROM macro_releases WHERE available_at > ?", [cutoff_dt]).fetchone()[0]
        missing_avail_macro = self.connection.execute("SELECT COUNT(*) FROM macro_releases WHERE available_at IS NULL").fetchone()[0]
        non_material_macro = self.connection.execute("SELECT COUNT(*) FROM macro_releases WHERE available_at <= ? AND (indicator IS NULL OR indicator = '')", [cutoff_dt]).fetchone()[0]

        pit_releases = self.get_macro_releases_pit(cutoff_dt)
        if event_id:
            selected_releases = [m for m in pit_releases if m.get("release_id") == event_id]
        else:
            selected_releases = pit_releases

        rel_ids_sorted = sorted([str(m["release_id"]) for m in selected_releases if "release_id" in m])
        rel_checksum = sha256(json.dumps(rel_ids_sorted).encode("utf-8")).hexdigest()

        # 2. Evidence claims
        scanned_claims = self.connection.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
        valid_id_claims = self.connection.execute("SELECT COUNT(*) FROM evidence_claims WHERE claim_id IS NOT NULL AND claim_id != ''").fetchone()[0]
        pit_eligible_claims = self.connection.execute("SELECT COUNT(*) FROM evidence_claims WHERE created_at <= ?", [cutoff_dt]).fetchone()[0]
        future_claims = self.connection.execute("SELECT COUNT(*) FROM evidence_claims WHERE created_at > ?", [cutoff_dt]).fetchone()[0]
        missing_avail_claims = self.connection.execute("SELECT COUNT(*) FROM evidence_claims WHERE created_at IS NULL").fetchone()[0]
        missing_ev_claims = self.connection.execute("SELECT COUNT(*) FROM evidence_claims WHERE created_at <= ? AND (source_excerpt IS NULL OR source_excerpt = '')", [cutoff_dt]).fetchone()[0]

        pit_claims = self.get_evidence_claims_pit(cutoff_dt)
        if event_id:
            target_doc_ids = {m.get("document_id") for m in selected_releases if m.get("document_id")}
            selected_claims = [c for c in pit_claims if c.get("claim_id") == event_id or c.get("document_id") in target_doc_ids or event_id in str(c.get("subject", ""))]
            if not selected_claims:
                selected_claims = pit_claims
        else:
            selected_claims = pit_claims

        claim_ids_sorted = sorted([str(c["claim_id"]) for c in selected_claims if "claim_id" in c])
        claim_checksum = sha256(json.dumps(claim_ids_sorted).encode("utf-8")).hexdigest()

        # 3. Sector state snapshots
        scanned_sec = self.connection.execute("SELECT COUNT(*) FROM sector_state_snapshots").fetchone()[0]
        valid_id_sec = self.connection.execute("SELECT COUNT(*) FROM sector_state_snapshots WHERE snapshot_id IS NOT NULL AND snapshot_id != ''").fetchone()[0]
        pit_eligible_sec = self.connection.execute("SELECT COUNT(*) FROM sector_state_snapshots WHERE as_of_timestamp <= ?", [cutoff_dt]).fetchone()[0]
        future_sec = self.connection.execute("SELECT COUNT(*) FROM sector_state_snapshots WHERE as_of_timestamp > ?", [cutoff_dt]).fetchone()[0]
        missing_avail_sec = self.connection.execute("SELECT COUNT(*) FROM sector_state_snapshots WHERE as_of_timestamp IS NULL").fetchone()[0]

        pit_sec = self.get_sector_state_snapshots_pit(cutoff_dt)
        sec_ids_sorted = sorted([str(s["snapshot_id"]) for s in pit_sec if "snapshot_id" in s])
        sec_checksum = sha256(json.dumps(sec_ids_sorted).encode("utf-8")).hexdigest()

        # 4. Source documents
        scanned_docs = self.connection.execute("SELECT COUNT(*) FROM cvm_documents").fetchone()[0]
        valid_id_docs = self.connection.execute("SELECT COUNT(*) FROM cvm_documents WHERE document_id IS NOT NULL AND document_id != ''").fetchone()[0]
        pit_eligible_docs = self.connection.execute("SELECT COUNT(*) FROM cvm_documents WHERE filing_available_at <= ? OR (filing_available_at IS NULL AND received_at <= ?)", [cutoff_dt, cutoff_dt]).fetchone()[0]
        future_docs = self.connection.execute("SELECT COUNT(*) FROM cvm_documents WHERE filing_available_at > ?", [cutoff_dt]).fetchone()[0]
        missing_avail_docs = self.connection.execute("SELECT COUNT(*) FROM cvm_documents WHERE filing_available_at IS NULL AND received_at IS NULL").fetchone()[0]

        pit_docs = self.get_source_documents_pit(cutoff_dt)
        if event_id:
            target_doc_ids = {m.get("document_id") for m in selected_releases if m.get("document_id")} | {c.get("document_id") for c in selected_claims if c.get("document_id")}
            selected_docs = [d for d in pit_docs if d.get("document_id") in target_doc_ids] if target_doc_ids else pit_docs
        else:
            selected_docs = pit_docs

        doc_ids_sorted = sorted([str(d["document_id"]) for d in selected_docs if "document_id" in d])
        doc_checksum = sha256(json.dumps(doc_ids_sorted).encode("utf-8")).hexdigest()

        return {
            "cutoff_timestamp": iso_cutoff,
            "filter_event_id": event_id,
            "macro_events": {
                "source_table_or_artifact": "macro_releases",
                "records_scanned": scanned_macro,
                "records_with_valid_id": valid_id_macro,
                "records_pit_eligible": pit_eligible_macro,
                "records_rejected_future": future_macro,
                "records_rejected_missing_available_at": missing_avail_macro,
                "records_rejected_missing_evidence": 0,
                "records_rejected_non_material": non_material_macro,
                "records_selected": len(selected_releases),
                "source_checksum": rel_checksum,
            },
            "evidence_claims": {
                "source_table_or_artifact": "evidence_claims",
                "records_scanned": scanned_claims,
                "records_with_valid_id": valid_id_claims,
                "records_pit_eligible": pit_eligible_claims,
                "records_rejected_future": future_claims,
                "records_rejected_missing_available_at": missing_avail_claims,
                "records_rejected_missing_evidence": missing_ev_claims,
                "records_rejected_non_material": 0,
                "records_selected": len(selected_claims),
                "source_checksum": claim_checksum,
            },
            "sector_state_snapshots": {
                "source_table_or_artifact": "sector_state_snapshots",
                "records_scanned": scanned_sec,
                "records_with_valid_id": valid_id_sec,
                "records_pit_eligible": pit_eligible_sec,
                "records_rejected_future": future_sec,
                "records_rejected_missing_available_at": missing_avail_sec,
                "records_rejected_missing_evidence": 0,
                "records_rejected_non_material": 0,
                "records_selected": len(pit_sec),
                "source_checksum": sec_checksum,
            },
            "source_documents": {
                "source_table_or_artifact": "cvm_documents",
                "records_scanned": scanned_docs,
                "records_with_valid_id": valid_id_docs,
                "records_pit_eligible": pit_eligible_docs,
                "records_rejected_future": future_docs,
                "records_rejected_missing_available_at": missing_avail_docs,
                "records_rejected_missing_evidence": 0,
                "records_rejected_non_material": 0,
                "records_selected": len(selected_docs),
                "source_checksum": doc_checksum,
            },
        }

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
                realtime_start, realtime_end, available_at, value,
                revision_number, is_initial_release, is_latest, record_checksum,
                ingestion_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                vint["vintage_id"], vint["series_code"], vint["source"],
                vint["reference_date"], vint["vintage_date"],
                vint.get("realtime_start"), vint.get("realtime_end"),
                vint.get("available_at", datetime.now(timezone.utc)),
                vint["value"], vint.get("revision_number", 0),
                vint.get("is_initial_release", True), vint.get("is_latest", True),
                vint.get("record_checksum", ""), vint["ingestion_run_id"],
                datetime.now(timezone.utc),
            ]
        )

        # Deterministically set is_latest = True ONLY for the record with MAX(vintage_date)
        self.connection.execute(
            """
            UPDATE macro_data_vintages
            SET is_latest = (
                vintage_date = (
                    SELECT MAX(v2.vintage_date)
                    FROM macro_data_vintages v2
                    WHERE v2.source = macro_data_vintages.source
                      AND v2.series_code = macro_data_vintages.series_code
                      AND v2.reference_date = macro_data_vintages.reference_date
                )
            )
            WHERE source = ? AND series_code = ? AND reference_date = ?
            """,
            [vint["source"], vint["series_code"], vint["reference_date"]]
        )
        return True

    def get_latest_vintage_date(self, source: str, series_code: str) -> Optional[date]:
        """Return the maximum vintage_date stored for a given series."""
        row = self.connection.execute(
            "SELECT MAX(vintage_date) FROM macro_data_vintages WHERE source = ? AND series_code = ?",
            [source, series_code]
        ).fetchone()
        if row and row[0]:
            val = row[0]
            if isinstance(val, str):
                return date.fromisoformat(val)
            if isinstance(val, date):
                return val
        return None

    def count_vintages_for_ref_date(self, source: str, series_code: str, ref_date: date) -> int:
        """Return count of existing vintages for a given series and reference_date."""
        row = self.connection.execute(
            "SELECT COUNT(*) FROM macro_data_vintages WHERE source = ? AND series_code = ? AND reference_date = ?",
            [source, series_code, ref_date]
        ).fetchone()
        return row[0] if row else 0

    def get_macro_releases_for_series(
        self, source: str, series_code: str, limit: int = 500, as_of_timestamp: Optional[datetime] = None
    ) -> list[dict]:
        """
        Return recent releases ordered by reference_date DESC.
        Uses ROW_NUMBER() partition to select at most 1 active release version per reference_date.
        """
        if as_of_timestamp:
            rows = self.connection.execute(
                """
                WITH available_versions AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY source, series_code, reference_date
                               ORDER BY available_at DESC, vintage_date DESC, revision_number DESC
                           ) AS rn
                    FROM macro_releases
                    WHERE source = ? AND series_code = ? AND available_at <= ?
                )
                SELECT release_id, reference_date, published_at, available_at,
                       actual_value, previous_value, consensus_value
                FROM available_versions
                WHERE rn = 1
                ORDER BY reference_date DESC
                LIMIT ?
                """,
                [source, series_code, as_of_timestamp, limit]
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                WITH available_versions AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY source, series_code, reference_date
                               ORDER BY available_at DESC, vintage_date DESC, revision_number DESC
                           ) AS rn
                    FROM macro_releases
                    WHERE source = ? AND series_code = ?
                )
                SELECT release_id, reference_date, published_at, available_at,
                       actual_value, previous_value, consensus_value
                FROM available_versions
                WHERE rn = 1
                ORDER BY reference_date DESC
                LIMIT ?
                """,
                [source, series_code, limit]
            ).fetchall()
        cols = ["release_id", "reference_date", "published_at", "available_at",
                "actual_value", "previous_value", "consensus_value"]
        return [dict(zip(cols, r)) for r in rows]

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
                direction, current_regime, evidence_ids, status, score_breakdown,
                source, series_code, ingestion_run_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                evt["event_id"], evt["event_type"], evt["indicator"], geography, affected_variables,
                evt["reference_date"], evt["detected_at"], evt["horizon_months"],
                evt.get("actual_value"), evt.get("expected_value"), evt.get("surprise_value"),
                evt["surprise_score"], evt["novelty_score"], evt["persistence_score"],
                evt["regime_shift_score"], evt["data_quality_score"],
                evt["direction"], evt["current_regime"], evidence_ids,
                evt.get("status", "PENDING"), score_breakdown,
                evt.get("source"), evt.get("series_code"), evt.get("ingestion_run_id"),
                datetime.now(timezone.utc),
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

    def get_latest_macro_event_run_id(self) -> Optional[str]:
        row = self.connection.execute(
            "SELECT ingestion_run_id FROM macro_event_candidates WHERE ingestion_run_id IS NOT NULL ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        row = self.connection.execute(
            "SELECT ingestion_run_id FROM macro_releases ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row and row[0] else None

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
            INSERT INTO company_ticker_map (
                ticker, cvm_code, cnpj, mapping_source, confidence, validated, created_at
                , legal_name, valid_from, valid_to, review_status, evidence_id, mapping_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker, cnpj) DO UPDATE SET
                cvm_code=excluded.cvm_code,
                mapping_source=excluded.mapping_source,
                confidence=excluded.confidence,
                validated=excluded.validated,
                legal_name=excluded.legal_name,
                valid_from=excluded.valid_from,
                valid_to=excluded.valid_to,
                review_status=excluded.review_status,
                evidence_id=excluded.evidence_id,
                mapping_version=excluded.mapping_version
            """,
            [
                mapping["ticker"], mapping.get("cvm_code"), mapping.get("cnpj"),
                mapping["mapping_source"], mapping["confidence"], mapping.get("validated", False),
                mapping.get("created_at", datetime.now(timezone.utc)),
                mapping.get("legal_name"), mapping.get("valid_from"), mapping.get("valid_to"),
                mapping.get("review_status", "UNREVIEWED"), mapping.get("evidence_id"),
                mapping.get("mapping_version"),
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
            if doc.get("availability_basis"):
                self.connection.execute(
                    """
                    UPDATE cvm_documents
                    SET received_at = ?, raw_zip_checksum = ?, ingestion_run_id = ?,
                        availability_basis = ?, source_url = ?
                        , filing_available_at = ?, resource_last_modified_at = ?,
                        collected_at = ?, availability_precision = ?
                    WHERE document_id = ?
                    """,
                    [
                        doc["received_at"], doc["raw_zip_checksum"], doc["ingestion_run_id"],
                        doc.get("availability_basis"), doc.get("source_url"),
                        doc.get("filing_available_at"), doc.get("resource_last_modified_at"),
                        doc.get("collected_at"), doc.get("availability_precision", "UNKNOWN"),
                        doc["document_id"],
                    ],
                )
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
                received_at, version, raw_zip_checksum, ingestion_run_id,
                availability_basis, source_url, filing_available_at,
                resource_last_modified_at, collected_at, availability_precision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                doc["document_id"], doc["document_type"], doc["cvm_code"], doc["cnpj"],
                doc["reference_date"], doc["received_at"], doc["version"],
                doc["raw_zip_checksum"], doc["ingestion_run_id"],
                doc.get("availability_basis"), doc.get("source_url"),
                doc.get("filing_available_at"), doc.get("resource_last_modified_at"),
                doc.get("collected_at"), doc.get("availability_precision", "UNKNOWN"),
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
            "outcome_label": row[22]
        }

    def save_sector_impact_candidate(self, cand: dict) -> bool:
        """Idempotent save of a SectorImpactCandidate."""
        existing = self.connection.execute(
            "SELECT COUNT(*) FROM sector_impact_candidates WHERE candidate_id = ?",
            [cand["candidate_id"]]
        ).fetchone()[0]
        if existing > 0:
            return False

        import json as _json
        causal_paths = _json.dumps(cand.get("causal_paths", []))
        direct_effects = _json.dumps(cand.get("direct_effects", []))
        second_order_effects = _json.dumps(cand.get("second_order_effects", []))
        invalidators = _json.dumps(cand.get("invalidators", []))

        self.connection.execute(
            """
            INSERT INTO sector_impact_candidates (
                candidate_id, event_id, event_type, sector, subsector,
                direction, impact_score, confidence, horizon_months,
                causal_paths, direct_effects, second_order_effects,
                positive_paths_count, negative_paths_count, conflict_detected, invalidators,
                status, detected_at, created_at
                , causal_root, event_strength, horizon_days, evidence_status,
                event_available_at, as_of_timestamp, run_id, source_event_run_id, graph_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                cand["candidate_id"], cand["event_id"], cand["event_type"], cand["sector"], cand.get("subsector"),
                cand["direction"], cand["impact_score"], cand["confidence"], cand.get("horizon_months", 3),
                causal_paths, direct_effects, second_order_effects,
                cand.get("positive_paths_count", 0), cand.get("negative_paths_count", 0),
                cand.get("conflict_detected", False), invalidators,
                cand["status"], cand["detected_at"], datetime.now(timezone.utc),
                cand.get("causal_root"), cand.get("event_strength"), cand.get("horizon_days"),
                cand.get("evidence_status"), cand.get("event_available_at"), cand.get("as_of_timestamp"),
                cand.get("run_id"), cand.get("source_event_run_id"), cand.get("graph_version")
            ]
        )
        return True

    def save_sector_state_snapshot(self, snapshot: dict) -> bool:
        """Idempotently persist the aggregate sector state for one run/as-of."""
        import json as _json
        if self.connection.execute(
            "SELECT COUNT(*) FROM sector_state_snapshots WHERE snapshot_id = ?", [snapshot["snapshot_id"]]
        ).fetchone()[0]:
            return False
        self.connection.execute(
            """
            INSERT INTO sector_state_snapshots (
                snapshot_id, sector, as_of_timestamp, net_impact, bullish_impact,
                bearish_impact, conflict_ratio, supporting_event_ids, opposing_event_ids,
                confidence, status, run_id, graph_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [snapshot["snapshot_id"], snapshot["sector"], snapshot["as_of_timestamp"],
             snapshot["net_impact"], snapshot["bullish_impact"], snapshot["bearish_impact"],
             snapshot["conflict_ratio"], _json.dumps(snapshot.get("supporting_event_ids", [])),
             _json.dumps(snapshot.get("opposing_event_ids", [])), snapshot["confidence"],
             snapshot["status"], snapshot["run_id"], snapshot["graph_version"], datetime.now(timezone.utc)]
        )
        return True

    def save_company_exposure_override(self, override: dict) -> bool:
        """Persist an immutable override; replay selection filters approved_at."""
        import json as _json
        if self.connection.execute(
            "SELECT COUNT(*) FROM company_exposure_overrides WHERE override_id = ?",
            [override["override_id"]],
        ).fetchone()[0]:
            return False
        self.connection.execute(
            """
            INSERT INTO company_exposure_overrides (
                override_id,ticker,field_name,previous_value,new_value,rationale,
                evidence_ids,approved_by,approved_at,methodology_version,run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                override["override_id"], override["ticker"], override["field_name"],
                _json.dumps(override.get("previous_value")), _json.dumps(override["new_value"]),
                override["rationale"], _json.dumps(override["evidence_ids"]),
                override["approved_by"], override["approved_at"],
                override["methodology_version"], override["run_id"],
            ],
        )
        return True

    def get_company_exposure_overrides_as_of(
        self, ticker: str, as_of_timestamp: datetime
    ) -> list[dict]:
        """Return only overrides already approved at the replay cutoff."""
        import json as _json
        cutoff = as_of_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        rows = self.connection.execute(
            """
            SELECT override_id,field_name,new_value,rationale,evidence_ids,
                   approved_by,approved_at,methodology_version,run_id
            FROM company_exposure_overrides
            WHERE ticker = ? AND approved_at <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ticker,field_name ORDER BY approved_at DESC,override_id DESC
            ) = 1
            """,
            [ticker, cutoff],
        ).fetchall()
        return [
            {
                "override_id": row[0], "field_name": row[1], "new_value": _json.loads(row[2]),
                "rationale": row[3], "evidence_ids": _json.loads(row[4]),
                "approved_by": row[5], "approved_at": row[6],
                "methodology_version": row[7], "run_id": row[8],
            }
            for row in rows
        ]

    def save_company_exposure_snapshot(self, exposure: dict) -> bool:
        import json as _json
        if self.connection.execute(
            "SELECT COUNT(*) FROM company_exposure_snapshots WHERE exposure_id = ?",
            [exposure["exposure_id"]],
        ).fetchone()[0]:
            return False
        identity = {
            "exposure_id", "ticker", "cvm_code", "sector", "as_of_timestamp",
            "reference_date", "exposure_version", "field_evidence", "missing_fields",
            "confidence", "evidence_quality_score", "completeness_score",
            "run_id", "created_at",
        }
        payload = {key: value for key, value in exposure.items() if key not in identity}
        self.connection.execute(
            """
            INSERT INTO company_exposure_snapshots (
                exposure_id,ticker,cvm_code,sector,as_of_timestamp,reference_date,
                exposure_version,exposure_payload,field_evidence,missing_fields,
                confidence,evidence_quality_score,completeness_score,run_id,created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                exposure["exposure_id"], exposure["ticker"], exposure["cvm_code"],
                exposure["sector"], exposure["as_of_timestamp"], exposure["reference_date"],
                exposure["exposure_version"], _json.dumps(payload),
                _json.dumps(exposure["field_evidence"], default=str),
                _json.dumps(exposure["missing_fields"]), exposure["confidence"],
                exposure.get("evidence_quality_score", 0),
                exposure.get("completeness_score", 0),
                exposure["run_id"], exposure["created_at"],
            ],
        )
        return True

    def save_company_impact_candidate(self, candidate: dict) -> bool:
        import json as _json
        exists = self.connection.execute(
            "SELECT COUNT(*) FROM company_impact_candidates WHERE candidate_id = ?",
            [candidate["candidate_id"]],
        ).fetchone()[0] > 0
        payload_keys = {
            "revenue_impact_score", "cost_impact_score", "debt_impact_score",
            "demand_impact_score", "net_company_impact",
            "supporting_event_ids", "opposing_event_ids", "source_path_ids",
            "causal_edge_ids", "factor_contributions",
            "missing_factor_exposures", "unsupported_factor_channels",
            "causal_evidence_status", "reason",
            "decision_policy", "known_component_count", "coverage_penalty",
            "materiality_threshold", "confidence_threshold",
        }
        self.connection.execute(
            """
            INSERT OR REPLACE INTO company_impact_candidates (
                candidate_id,ticker,sector_snapshot_id,company_exposure_id,
                as_of_timestamp,impact_payload,confidence,conflict_ratio,
                missing_exposures,status,run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                candidate["candidate_id"], candidate["ticker"],
                candidate["sector_snapshot_id"], candidate["company_exposure_id"],
                candidate["as_of_timestamp"],
                _json.dumps({key: candidate.get(key) for key in payload_keys}),
                candidate["confidence"], candidate["conflict_ratio"],
                _json.dumps(candidate["missing_exposures"]), candidate["status"],
                candidate["run_id"],
            ],
        )
        return not exists

    def save_financial_baseline(self, baseline: dict) -> bool:
        exists = self.connection.execute(
            "SELECT COUNT(*) FROM financial_baseline_snapshots WHERE baseline_id=?",
            [baseline["baseline_id"]],
        ).fetchone()[0] > 0
        identity = {
            "baseline_id", "ticker", "cvm_code", "as_of_timestamp",
            "latest_quarter", "methodology_version", "field_evidence",
            "missing_fields", "confidence", "run_id", "created_at",
        }
        payload = {key: value for key, value in baseline.items() if key not in identity}
        self.connection.execute(
            """
            INSERT OR REPLACE INTO financial_baseline_snapshots (
                baseline_id,ticker,cvm_code,as_of_timestamp,latest_quarter,
                baseline_payload,field_evidence,missing_fields,confidence,
                methodology_version,run_id,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                baseline["baseline_id"], baseline["ticker"], baseline["cvm_code"],
                baseline["as_of_timestamp"], baseline["latest_quarter"],
                json.dumps(payload, default=str),
                json.dumps(baseline["field_evidence"], default=str),
                json.dumps(baseline["missing_fields"]),
                baseline["confidence"], baseline["methodology_version"],
                baseline["run_id"], baseline["created_at"],
            ],
        )
        return not exists

    def save_economic_shock_scenario(self, scenario: dict, run_id: str) -> bool:
        exists = self.connection.execute(
            "SELECT COUNT(*) FROM economic_shock_scenarios WHERE scenario_id=?",
            [scenario["scenario_id"]],
        ).fetchone()[0] > 0
        self.connection.execute(
            """
            INSERT OR REPLACE INTO economic_shock_scenarios (
                scenario_id,factor,scenario_case,magnitude,unit,horizon_years,
                as_of_timestamp,scenario_payload,methodology_version,run_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            [
                scenario["scenario_id"], scenario["factor"], scenario["shock_case"],
                scenario["absolute_magnitude"], scenario["unit"],
                scenario["horizon_years"],
                scenario["as_of_timestamp"], json.dumps(scenario, default=str),
                scenario["methodology_version"], run_id,
            ],
        )
        return not exists

    def save_financial_scenario_outcome(self, outcome: dict) -> bool:
        exists = self.connection.execute(
            "SELECT COUNT(*) FROM financial_scenario_outcomes WHERE outcome_id=?",
            [outcome["outcome_id"]],
        ).fetchone()[0] > 0
        self.connection.execute(
            """
            INSERT OR REPLACE INTO financial_scenario_outcomes (
                outcome_id,ticker,scenario_case,as_of_timestamp,baseline_id,
                company_impact_candidate_id,outcome_payload,confidence,status,
                reason,run_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                outcome["outcome_id"], outcome["ticker"], outcome["case"],
                outcome["as_of_timestamp"], outcome["baseline_id"],
                outcome["company_impact_candidate_id"],
                json.dumps(outcome, default=str), outcome["confidence"],
                outcome["status"], outcome["reason"], outcome["run_id"],
            ],
        )
        return not exists

    def save_financial_bridge_calibration(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO financial_bridge_calibrations
            (calibration_id,ticker,bridge,calibration_payload,confidence,status,run_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            [
                item["calibration_id"], item["ticker"], item["bridge"],
                json.dumps(item, default=str), item["confidence"],
                item["calibration_status"], item["run_id"],
            ],
        )

    def save_normalized_cash_flow_snapshot(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO normalized_cash_flow_snapshots
            (snapshot_id,ticker,as_of_timestamp,snapshot_payload,confidence,run_id)
            VALUES (?,?,?,?,?,?)
            """,
            [
                item["snapshot_id"], item["ticker"], item["as_of_timestamp"],
                json.dumps(item, default=str), item["confidence"], item["run_id"],
            ],
        )

    def save_causal_conflict_diagnostic(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO causal_conflict_diagnostics
            (diagnostic_id,ticker,factor,as_of_timestamp,diagnostic_payload,
             classification,run_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            [
                item["diagnostic_id"], item["ticker"], item["factor"],
                item["as_of_timestamp"], json.dumps(item, default=str),
                item["classification"], item["run_id"],
            ],
        )

    def save_valuation_readiness_assessment(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO valuation_readiness_assessments
            (assessment_id,ticker,as_of_timestamp,assessment_payload,status,
             valuation_eligible,run_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            [
                item["assessment_id"], item["ticker"], item["as_of_timestamp"],
                json.dumps(item, default=str), item["status"],
                item["valuation_eligible"], item["run_id"],
            ],
        )

    def save_market_snapshot_pit(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO market_snapshots_pit
            (market_snapshot_id,ticker,assessment_as_of,price_as_of,
             price_available_at,share_count_as_of,share_count_available_at,
             as_of_timestamp,available_at,price,
             share_count,share_count_basis,currency,source_id,market_data_version,
             security_type,equity_value_basis,price_source_file,
             price_source_checksum,price_layout_version,price_record_hash,
             share_document_id,share_document_version,share_document_checksum,
             share_section,snapshot_payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                item["market_snapshot_id"], item["ticker"], item["assessment_as_of"],
                item["price_as_of"], item["price_available_at"],
                item["share_count_as_of"], item["share_count_available_at"],
                item["as_of_timestamp"], item["available_at"], item["price"], item["share_count"],
                item["share_count_basis"], item["currency"], item["source_id"],
                item["market_data_version"], item["security_type"],
                item["equity_value_basis"], item.get("price_source_file"),
                item.get("price_source_checksum"), item.get("price_layout_version"),
                item.get("price_record_hash"), item.get("share_document_id"),
                item.get("share_document_version"), item.get("share_document_checksum"),
                item.get("share_section"), json.dumps(item, default=str),
            ],
        )

    def save_pit_security_mapping(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO pit_security_mappings
            (mapping_id,ticker,cvm_code,cnpj,isin,security_type,valid_from,
             valid_to,mapping_source,mapping_available_at,mapping_checksum,
             mapping_payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                item["mapping_id"], item["ticker"], item["cvm_code"],
                item["cnpj"], item["isin"], item["security_type"],
                item["valid_from"], item.get("valid_to"), item["mapping_source"],
                item["mapping_available_at"], item["mapping_checksum"],
                json.dumps(item, default=str),
            ],
        )

    def save_historical_valuation_observation(self, item: dict) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO historical_valuation_observations
            (observation_id,ticker,valuation_date,market_snapshot_id,
             financial_baseline_id,methodology_version,observation_payload)
            VALUES (?,?,?,?,?,?,?)
            """,
            [
                item["observation_id"], item["ticker"], item["valuation_date"],
                item["market_snapshot_id"], item["financial_baseline_id"],
                item["methodology_version"], json.dumps(item, default=str),
            ],
        )

    def get_sector_impact_candidates(self, status: Optional[str] = None) -> list[dict]:
        if status:
            rows = self.connection.execute(
                """
                SELECT candidate_id, event_id, event_type, sector, subsector,
                       direction, impact_score, confidence, horizon_months,
                       causal_paths, direct_effects, second_order_effects,
                       positive_paths_count, negative_paths_count, conflict_detected, invalidators,
                       status, detected_at
                FROM sector_impact_candidates
                WHERE status = ?
                ORDER BY detected_at DESC
                """,
                [status]
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT candidate_id, event_id, event_type, sector, subsector,
                       direction, impact_score, confidence, horizon_months,
                       causal_paths, direct_effects, second_order_effects,
                       positive_paths_count, negative_paths_count, conflict_detected, invalidators,
                       status, detected_at
                FROM sector_impact_candidates
                ORDER BY detected_at DESC
                """
            ).fetchall()

        cols = [
            "candidate_id", "event_id", "event_type", "sector", "subsector",
            "direction", "impact_score", "confidence", "horizon_months",
            "causal_paths", "direct_effects", "second_order_effects",
            "positive_paths_count", "negative_paths_count", "conflict_detected", "invalidators",
            "status", "detected_at"
        ]
        return [dict(zip(cols, r)) for r in rows]

    def save_research_decision_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Persists a research decision snapshot into DuckDB idempotently."""
        decision_id = snapshot["decision_id"]
        # Check if already exists for idempotency
        existing = self.connection.execute(
            "SELECT 1 FROM research_decision_snapshots WHERE decision_id = ?",
            [decision_id]
        ).fetchone()
        if existing:
            return

        as_of_val = snapshot.get("as_of_timestamp")
        if isinstance(as_of_val, str):
            as_of_ts = datetime.fromisoformat(as_of_val.replace("Z", "+00:00"))
        elif isinstance(as_of_val, datetime):
            as_of_ts = as_of_val
        else:
            as_of_ts = datetime.now(timezone.utc)

        self.connection.execute(
            """
            INSERT INTO research_decision_snapshots
            (decision_id, ticker, as_of_timestamp, decision, confidence, confidence_tier,
             canonical_payload_json, input_ids_json, methodology_version, execution_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                decision_id,
                snapshot["ticker"],
                as_of_ts,
                snapshot["decision"],
                float(snapshot["confidence"]),
                snapshot["confidence_tier"],
                json.dumps(snapshot, default=str),
                json.dumps(snapshot.get("input_ids", {}), default=str),
                snapshot.get("methodology_version", "4E.3-research-decision-synthesis-v1"),
                snapshot.get("execution_mode", "BLOCKED_MISSING_UPSTREAM_INPUT"),
            ],
        )

    def get_research_decision_snapshots(self, ticker: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieves research decision snapshots from DuckDB."""
        if ticker:
            rows = self.connection.execute(
                """
                SELECT decision_id, ticker, as_of_timestamp, decision, confidence, confidence_tier,
                       canonical_payload_json, input_ids_json, methodology_version, created_at
                FROM research_decision_snapshots
                WHERE ticker = ?
                ORDER BY as_of_timestamp DESC
                """,
                [ticker]
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT decision_id, ticker, as_of_timestamp, decision, confidence, confidence_tier,
                       canonical_payload_json, input_ids_json, methodology_version, created_at
                FROM research_decision_snapshots
                ORDER BY as_of_timestamp DESC
                """
            ).fetchall()

        result = []
        for r in rows:
            payload = json.loads(r[6])
            result.append(payload)
        return result

    def get_latest_research_decision_snapshot_pit(self, ticker: str, as_of_timestamp: datetime) -> Optional[dict[str, Any]]:
        """Retrieves the latest ResearchDecisionSnapshot for ticker strictly available at or before as_of_timestamp."""
        rows = self.connection.execute(
            """
            SELECT canonical_payload_json
            FROM research_decision_snapshots
            WHERE ticker = ? AND as_of_timestamp <= ?
            ORDER BY as_of_timestamp DESC
            LIMIT 1
            """,
            [ticker, as_of_timestamp]
        ).fetchone()
        if rows:
            return json.loads(rows[0])
        return None

    def get_historical_market_quotes(self, ticker: str, max_as_of_timestamp: datetime) -> list[dict[str, Any]]:
        """Retrieves historical market quotes for ticker strictly up to max_as_of_timestamp."""
        try:
            cutoff_date = max_as_of_timestamp.date()
            rows = self.connection.execute(
                """
                SELECT trade_date, close_price, quote_factor, isin
                FROM historical_market_quotes
                WHERE ticker = ? AND trade_date <= ?
                ORDER BY trade_date ASC
                """,
                [ticker, cutoff_date]
            ).fetchall()
            return [
                {
                    "trade_date": str(r[0]),
                    "close_price": float(r[1]),
                    "volume_brl": float(r[1] * 1000000.0),  # volume estimate when raw trade volume is absent
                }
                for r in rows
            ]
        except Exception:
            return []

    def save_research_timing_risk_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Persists a research timing risk snapshot into DuckDB idempotently."""
        timing_risk_id = snapshot["timing_risk_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM research_timing_risk_snapshots WHERE timing_risk_id = ?",
            [timing_risk_id]
        ).fetchone()
        if existing:
            return

        as_of_val = snapshot.get("as_of_timestamp")
        if isinstance(as_of_val, str):
            as_of_ts = datetime.fromisoformat(as_of_val.replace("Z", "+00:00"))
        elif isinstance(as_of_val, datetime):
            as_of_ts = as_of_val
        else:
            as_of_ts = datetime.now(timezone.utc)

        self.connection.execute(
            """
            INSERT INTO research_timing_risk_snapshots
            (timing_risk_id, ticker, as_of_timestamp, research_decision_id,
             timing_classification, risk_classification, confidence,
             canonical_payload_json, input_ids_json, methodology_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                timing_risk_id,
                snapshot["ticker"],
                as_of_ts,
                snapshot["research_decision_id"],
                snapshot["timing_classification"],
                snapshot["risk_classification"],
                float(snapshot["confidence"]),
                json.dumps(snapshot, default=str),
                json.dumps(snapshot.get("input_ids", {}), default=str),
                snapshot.get("methodology_version", "4F.1-research-timing-risk-v1"),
            ],
        )

    def get_research_timing_risk_snapshots(self, ticker: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieves research timing risk snapshots from DuckDB."""
        if ticker:
            rows = self.connection.execute(
                """
                SELECT timing_risk_id, ticker, as_of_timestamp, research_decision_id,
                       timing_classification, risk_classification, confidence,
                       canonical_payload_json, input_ids_json, methodology_version, created_at
                FROM research_timing_risk_snapshots
                WHERE ticker = ?
                ORDER BY as_of_timestamp DESC
                """,
                [ticker]
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT timing_risk_id, ticker, as_of_timestamp, research_decision_id,
                       timing_classification, risk_classification, confidence,
                       canonical_payload_json, input_ids_json, methodology_version, created_at
                FROM research_timing_risk_snapshots
                ORDER BY as_of_timestamp DESC
                """
            ).fetchall()

        result = []
        for r in rows:
            payload = json.loads(r[7])
            result.append(payload)
        return result

    def save_historical_replay_run(self, replay_run: dict[str, Any]) -> None:
        """Persists a historical replay run into DuckDB idempotently."""
        run_id = replay_run["replay_run_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM historical_replay_runs WHERE replay_run_id = ?",
            [run_id]
        ).fetchone()
        if existing:
            return

        self.connection.execute(
            """
            INSERT INTO historical_replay_runs
            (replay_run_id, start_date, end_date, initial_capital, portfolio_policy_id, status, canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                datetime.fromisoformat(replay_run["start_date"].replace("Z", "+00:00")),
                datetime.fromisoformat(replay_run["end_date"].replace("Z", "+00:00")),
                float(replay_run["initial_capital"]),
                replay_run["portfolio_policy_id"],
                replay_run["status"],
                json.dumps(replay_run, default=str),
            ],
        )

    def save_paper_allocation_event(self, event: dict[str, Any]) -> None:
        """Persists a paper allocation event into DuckDB idempotently."""
        event_id = event["allocation_event_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM paper_allocation_events WHERE allocation_event_id = ?",
            [event_id]
        ).fetchone()
        if existing:
            return

        self.connection.execute(
            """
            INSERT INTO paper_allocation_events
            (allocation_event_id, portfolio_id, ticker, event_type, research_decision_id,
             timing_risk_id, execution_session, execution_price, open_price, quote_record_id,
             isin, source_checksum, target_weight, executed_weight, quantity_simulated,
             gross_value, transaction_cost, slippage_cost, reason, canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event_id,
                event["portfolio_id"],
                event["ticker"],
                event["event_type"],
                event["research_decision_id"],
                event["timing_risk_id"],
                event["execution_session"],
                float(event["execution_price"]) if event.get("execution_price") is not None else None,
                float(event["open_price"]) if event.get("open_price") is not None else None,
                event.get("quote_record_id", ""),
                event.get("isin", ""),
                event.get("source_checksum", ""),
                float(event["target_weight"]),
                float(event["executed_weight"]),
                float(event["quantity_simulated"]),
                float(event["gross_value"]),
                float(event["transaction_cost"]),
                float(event["slippage_cost"]),
                event["reason"],
                json.dumps(event, default=str),
            ],
        )

    def save_scenario_seed_package(self, seed: dict[str, Any]) -> None:
        """Persists a ScenarioSeedPackage into DuckDB idempotently."""
        seed_id = seed["seed_package_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM scenario_seed_packages WHERE seed_package_id = ?",
            [seed_id]
        ).fetchone()
        if existing:
            return

        self.connection.execute(
            """
            INSERT INTO scenario_seed_packages
            (seed_package_id, as_of_timestamp, prompt_template_version, canonical_payload_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                seed_id,
                datetime.fromisoformat(seed["as_of_timestamp"].replace("Z", "+00:00")),
                seed.get("prompt_template_version", "5A.1-mirofish-seed-v1"),
                json.dumps(seed, default=str),
            ],
        )

    def save_mirofish_simulation_run(self, run: dict[str, Any]) -> None:
        """Persists a MiroFishSimulationRun into DuckDB idempotently."""
        run_id = run["simulation_run_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM mirofish_simulation_runs WHERE simulation_run_id = ?",
            [run_id]
        ).fetchone()
        if existing:
            return

        self.connection.execute(
            """
            INSERT INTO mirofish_simulation_runs
            (simulation_run_id, seed_package_id, status, prompt_hash, input_checksum, canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                run["seed_package_id"],
                run["status"],
                run["prompt_hash"],
                run["input_checksum"],
                json.dumps(run, default=str),
            ],
        )

    def save_scenario_set(self, scenario_set: dict[str, Any]) -> None:
        """Persists a ScenarioSet into DuckDB idempotently."""
        set_id = scenario_set["scenario_set_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM scenario_sets WHERE scenario_set_id = ?",
            [set_id]
        ).fetchone()
        if existing:
            return

        self.connection.execute(
            """
            INSERT INTO scenario_sets
            (scenario_set_id, event_id, as_of_timestamp, canonical_payload_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                set_id,
                scenario_set["event_id"],
                datetime.fromisoformat(scenario_set["as_of_timestamp"].replace("Z", "+00:00")),
                json.dumps(scenario_set, default=str),
            ],
        )

    def save_scenario_hypothesis(self, hypothesis: dict[str, Any]) -> None:
        """Persists a ScenarioHypothesis into DuckDB idempotently."""
        hyp_id = hypothesis["hypothesis_id"]
        existing = self.connection.execute(
            "SELECT 1 FROM scenario_hypotheses WHERE hypothesis_id = ?",
            [hyp_id]
        ).fetchone()
        if existing:
            return

        conf = hypothesis.get("confidence")
        conf_val = float(conf) if conf is not None else None

        self.connection.execute(
            """
            INSERT INTO scenario_hypotheses
            (hypothesis_id, simulation_run_id, scenario_type, verification_status, confidence, canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                hyp_id,
                hypothesis["simulation_run_id"],
                hypothesis.get("scenario_type", "UNKNOWN"),
                hypothesis.get("verification_status", "UNVERIFIED"),
                conf_val,
                json.dumps(hypothesis, default=str),
            ],
        )

    def save_scenario_hypothesis_review(self, review: dict[str, Any]) -> None:
        """Append a review without mutating the immutable hypothesis payload."""
        self.connection.execute(
            """
            INSERT INTO scenario_hypothesis_reviews
            (review_id, hypothesis_id, simulation_run_id, reviewer_type, reviewer_id,
             review_decision, review_status, review_confidence, fact_review_hash,
             canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (review_id) DO NOTHING
            """,
            [
                review["review_id"], review["hypothesis_id"], review["simulation_run_id"],
                review["reviewer_type"], review.get("reviewed_by", "NOT_EXPOSED"),
                review["review_decision"], review["review_status"],
                review.get("review_confidence"), review["fact_review_hash"],
                json.dumps(review, ensure_ascii=False, sort_keys=True, default=str),
            ],
        )

    def save_scenario_hypothesis_validation(self, validation: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO scenario_hypothesis_validations
            (validation_id, hypothesis_id, validation_status, validator_type, canonical_payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (validation_id) DO NOTHING
            """,
            [
                validation["validation_id"], validation["hypothesis_id"],
                validation["validation_status"], validation["validator_type"],
                json.dumps(validation, ensure_ascii=False, sort_keys=True, default=str),
            ],
        )

    def save_scenario_hypothesis_binding(self, binding: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO scenario_hypothesis_bindings
            (binding_id, hypothesis_id, binding_status, canonical_payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (binding_id) DO NOTHING
            """,
            [
                binding["binding_id"], binding["hypothesis_id"],
                binding["binding_status"],
                json.dumps(binding, ensure_ascii=False, sort_keys=True, default=str),
            ],
        )

    def save_raw_mirofish_report(self, report_record: dict[str, Any]) -> None:
        """Persists raw MiroFish report metadata into DuckDB idempotently."""
        report_id = report_record["report_id"]
        existing = self.connection.execute(
            "SELECT content_checksum FROM raw_mirofish_reports WHERE report_id = ?",
            [report_id]
        ).fetchone()
        if existing and existing[0] == report_record["content_checksum"]:
            return

        retrieved_at_val = report_record.get("retrieved_at")
        if isinstance(retrieved_at_val, str):
            retrieved_ts = datetime.fromisoformat(retrieved_at_val.replace("Z", "+00:00"))
        elif isinstance(retrieved_at_val, datetime):
            retrieved_ts = retrieved_at_val
        else:
            retrieved_ts = datetime.now(timezone.utc)

        values = [
            report_id,
            report_record.get("simulation_id", ""),
            report_record.get("project_id", ""),
            report_record["content_checksum"],
            int(report_record["byte_size"]),
            report_record.get("mime_type", "application/json"),
            retrieved_ts,
            report_record.get("source_endpoint", "/api/report/list"),
            report_record.get("file_path", ""),
            report_record.get("canonical_payload_json", "{}"),
        ]
        if existing:
            self.connection.execute(
                """
                UPDATE raw_mirofish_reports
                SET simulation_id = ?, project_id = ?, content_checksum = ?, byte_size = ?,
                    mime_type = ?, retrieved_at = ?, source_endpoint = ?, file_path = ?,
                    canonical_payload_json = ?
                WHERE report_id = ?
                """,
                values[1:] + [report_id],
            )
            return

        self.connection.execute(
            """
            INSERT INTO raw_mirofish_reports
            (report_id, simulation_id, project_id, content_checksum, byte_size,
             mime_type, retrieved_at, source_endpoint, file_path, canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def close(self) -> None:
        self.connection.close()


