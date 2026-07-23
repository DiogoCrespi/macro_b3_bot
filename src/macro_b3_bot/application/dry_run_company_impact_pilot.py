"""Restricted integration dry-run for KLBN11 and SLCE3 only."""
from __future__ import annotations

from datetime import timezone
import json

from macro_b3_bot.application.evaluate_company_impacts import CompanyImpactEngine
from macro_b3_bot.application.transport_company_channels import CompanyChannelTransport
from macro_b3_bot.domain.causal_models import SectorImpactCandidate, SectorStateSnapshot
from macro_b3_bot.domain.company_exposure_models import CompanyExposureSnapshot
from macro_b3_bot.infrastructure.store import DatabaseStore


class CompanyImpactPilotDryRun:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store

    def run(
        self, exposure_run_id: str, selection_run_id: str, sector_run_id: str
    ) -> dict[str, object]:
        results = []
        for ticker in ("KLBN11", "SLCE3"):
            exposure = self._exposure(ticker, exposure_run_id)
            if exposure is None:
                results.append({"ticker": ticker, "status": "BLOCKED_MISSING_SNAPSHOT"})
                continue
            sector = self._sector(exposure.sector, sector_run_id)
            if sector is None:
                results.append({"ticker": ticker, "status": "BLOCKED_MISSING_SECTOR"})
                continue
            if sector.status == "SECTOR_STATE_NO_ACTIVE_SIGNAL":
                candidate = CompanyImpactEngine("dry_run_4c4").evaluate(
                    sector, exposure, None, exposure.as_of_timestamp
                )
                results.append(candidate.model_dump(mode="json"))
                continue
            reviews = self.store.connection.execute(
                """
                SELECT review_status,COUNT(*)
                FROM company_macro_exposure_facts
                WHERE selection_run_id=? AND ticker=? AND is_active=TRUE
                GROUP BY review_status
                """,
                [selection_run_id, ticker],
            ).fetchall()
            review_counts = dict(reviews)
            if review_counts.get("HUMAN_APPROVED", 0) < 3:
                results.append({
                    "ticker": ticker,
                    "status": "BLOCKED_HUMAN_REVIEW",
                    "review_counts": review_counts,
                    "candidate_generated": False,
                })
                continue
            channels = CompanyChannelTransport().from_sector_candidates(
                self._sector_candidates(exposure.sector, sector_run_id)
            )
            candidate = CompanyImpactEngine("dry_run_4c4").evaluate(
                sector, exposure, None, exposure.as_of_timestamp,
                factor_channels=channels,
            )
            results.append(candidate.model_dump(mode="json"))
        return {
            "exposure_run_id": exposure_run_id,
            "selection_run_id": selection_run_id,
            "sector_run_id": sector_run_id,
            "results": results,
            "valuation_enabled": False,
            "buy_enabled": False,
            "orders_enabled": False,
        }

    def _exposure(
        self, ticker: str, exposure_run_id: str
    ) -> CompanyExposureSnapshot | None:
        row = self.store.connection.execute(
            """
            SELECT exposure_id,ticker,cvm_code,sector,as_of_timestamp,reference_date,
                   exposure_version,exposure_payload,field_evidence,missing_fields,
                   confidence,evidence_quality_score,completeness_score,run_id,created_at
            FROM company_exposure_snapshots
            WHERE ticker=? AND run_id=? ORDER BY created_at DESC LIMIT 1
            """,
            [ticker, exposure_run_id],
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[7])
        return CompanyExposureSnapshot.model_validate({
            "exposure_id": row[0], "ticker": row[1], "cvm_code": row[2],
            "sector": row[3],
            "as_of_timestamp": row[4].replace(tzinfo=timezone.utc),
            "reference_date": row[5], "exposure_version": row[6], **payload,
            "field_evidence": json.loads(row[8]),
            "missing_fields": json.loads(row[9]), "confidence": row[10],
            "evidence_quality_score": row[11], "completeness_score": row[12],
            "run_id": row[13], "created_at": row[14].replace(tzinfo=timezone.utc),
        })

    def _sector(
        self, sector: str, sector_run_id: str
    ) -> SectorStateSnapshot | None:
        row = self.store.connection.execute(
            """
            SELECT snapshot_id,sector,as_of_timestamp,net_impact,bullish_impact,
                   bearish_impact,conflict_ratio,supporting_event_ids,
                   opposing_event_ids,confidence,status,run_id,graph_version
            FROM sector_state_snapshots
            WHERE sector=? AND run_id=? LIMIT 1
            """,
            [sector, sector_run_id],
        ).fetchone()
        if not row:
            return None
        return SectorStateSnapshot(
            snapshot_id=row[0], sector=row[1],
            as_of_timestamp=row[2].replace(tzinfo=timezone.utc),
            net_impact=row[3], bullish_impact=row[4], bearish_impact=row[5],
            conflict_ratio=row[6], supporting_event_ids=json.loads(row[7]),
            opposing_event_ids=json.loads(row[8]), confidence=row[9],
            status=row[10], run_id=row[11], graph_version=row[12],
        )

    def _sector_candidates(
        self, sector: str, sector_run_id: str
    ) -> list[SectorImpactCandidate]:
        cursor = self.store.connection.execute(
            """
            SELECT * FROM sector_impact_candidates
            WHERE sector=? AND run_id=?
            """,
            [sector, sector_run_id],
        )
        columns = [item[0] for item in cursor.description]
        candidates = []
        json_fields = {
            "causal_paths", "direct_effects", "second_order_effects", "invalidators"
        }
        for row in cursor.fetchall():
            item = dict(zip(columns, row, strict=True))
            for field in json_fields:
                item[field] = json.loads(item[field])
            for field in ("detected_at", "event_available_at", "as_of_timestamp"):
                if item.get(field) is not None:
                    item[field] = item[field].replace(tzinfo=timezone.utc)
            candidates.append(SectorImpactCandidate.model_validate(item))
        return candidates
