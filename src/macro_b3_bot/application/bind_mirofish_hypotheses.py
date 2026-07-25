"""Sprint 5B: bind MiroFish hypotheses to deterministic PIT causal inputs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from macro_b3_bot.infrastructure.store import DatabaseStore


class MiroFishHypothesisBinder:
    """Resolve only IDs present in the PIT store; never invent causal links."""

    def __init__(self, store: DatabaseStore):
        self.store = store

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def bind(self, hypothesis: dict[str, Any], as_of: datetime) -> dict[str, Any]:
        event_ids = set(hypothesis.get("macro_event_ids", []))
        sector_ids = set(hypothesis.get("sector_state_ids", []))
        # MiroFish seeds may carry release IDs while the causal engine stores
        # its own event IDs. Resolve that identity bridge before querying paths.
        linked_event_rows = self.store.connection.execute(
            "SELECT event_id, release_id FROM macro_event_evidence_links WHERE release_id IN (SELECT UNNEST(?))",
            [list(event_ids)],
        ).fetchall() if event_ids else []
        resolved_event_ids = event_ids | {str(row[0]) for row in linked_event_rows}
        event_rows = self.store.connection.execute(
            "SELECT release_id, available_at FROM macro_releases WHERE release_id IN (SELECT UNNEST(?))",
            [list(event_ids)],
        ).fetchall() if event_ids else []
        sector_rows = self.store.connection.execute(
            "SELECT snapshot_id, sector, as_of_timestamp, conflict_ratio, supporting_event_ids, opposing_event_ids "
            "FROM sector_state_snapshots WHERE snapshot_id IN (SELECT UNNEST(?))",
            [list(sector_ids)],
        ).fetchall() if sector_ids else []

        # Claims must be explicitly declared by the seed/hypothesis.  Text or
        # numeric overlap is not evidence of semantic identity (e.g. 0.45 can
        # occur in unrelated indicators).
        declared_claim_ids = {str(x) for x in hypothesis.get("supporting_evidence_claim_ids", []) if x}
        claims = [
            claim for claim in self.store.get_evidence_claims_pit(as_of)
            if str(claim.get("claim_id")) in declared_claim_ids
        ]
        claim_ids = sorted({str(c["claim_id"]) for c in claims if c.get("claim_id")})
        missing_claim_ids = sorted(declared_claim_ids - set(claim_ids))

        path_ids: set[str] = set()
        edge_ids: set[str] = set()
        contradiction_ids: set[str] = set()
        candidate_rows = []
        declared_factors = {str(value).casefold() for value in hypothesis.get("macro_factors", []) if value}
        declared_sector_names = {str(row[1]).casefold() for row in sector_rows if row[1]}
        rejected_event = bool(self.store.connection.execute(
            "SELECT 1 FROM macro_event_candidates WHERE event_id IN (SELECT UNNEST(?)) "
            "AND status='MACRO_EVENT_REJECTED' LIMIT 1",
            [list(resolved_event_ids)],
        ).fetchone()) if resolved_event_ids else False
        if resolved_event_ids:
            candidate_rows = self.store.connection.execute(
                "SELECT candidate_id, causal_paths, conflict_detected, event_available_at, as_of_timestamp, status "
                "FROM sector_impact_candidates WHERE event_id IN (SELECT UNNEST(?))",
                [list(resolved_event_ids)],
            ).fetchall()
        for candidate_id, paths_json, conflict, event_available, candidate_as_of, candidate_status in candidate_rows:
            rejected_event = rejected_event or candidate_status == "SECTOR_IMPACT_REJECTED"
            try:
                paths = json.loads(paths_json or "[]")
            except json.JSONDecodeError:
                paths = []
            for path in paths:
                if isinstance(path, dict):
                    path_factor = str(path.get("factor", "")).casefold()
                    path_sector = str(path.get("sector", path.get("final_sector", ""))).casefold()
                    if path_factor and declared_factors and not any(
                        path_factor in factor or factor in path_factor for factor in declared_factors
                    ):
                        continue
                    if path_sector and declared_sector_names and not any(
                        path_sector in sector or sector in path_sector for sector in declared_sector_names
                    ):
                        continue
                    if path.get("path_id"):
                        path_ids.add(str(path["path_id"]))
                    edge_ids.update(str(x) for x in path.get("causal_edge_ids", []) if x)
            if conflict:
                contradiction_ids.add(str(candidate_id))

        temporal_ok = bool(event_rows and sector_rows)
        pit_inputs_complete = bool(event_rows and sector_rows)
        for _, available_at in event_rows:
            temporal_ok = temporal_ok and self._aware(available_at) is not None and self._aware(available_at) <= self._aware(as_of)
        for _, _, snapshot_as_of, *_ in sector_rows:
            temporal_ok = temporal_ok and self._aware(snapshot_as_of) is not None and self._aware(snapshot_as_of) <= self._aware(as_of)
        for _, _, _, event_available, candidate_as_of, _ in candidate_rows:
            if event_available is None or candidate_as_of is None:
                pit_inputs_complete = False
            if event_available and self._aware(event_available) > self._aware(as_of):
                temporal_ok = False
            if candidate_as_of and self._aware(candidate_as_of) > self._aware(as_of):
                temporal_ok = False

        if not event_rows or not sector_rows:
            temporal_status = "INSUFFICIENT_DATA"
        else:
            temporal_status = "CONSISTENT" if temporal_ok else "INCONSISTENT"
        if contradiction_ids:
            contradiction_status = "CONTRADICTION_DETECTED"
        elif sector_rows and any(float(row[3] or 0) > 0 for row in sector_rows):
            contradiction_status = "SECTOR_CONFLICT_PRESENT"
        else:
            contradiction_status = "NO_CONTRADICTION_DETECTED"
        if path_ids and not rejected_event and not contradiction_ids and not missing_claim_ids and pit_inputs_complete and temporal_ok:
            binding_status = "BOUND"
        elif event_ids and not event_rows:
            binding_status = "BLOCKED_EVENT_NOT_FOUND"
        elif rejected_event:
            binding_status = "REJECTED_MACRO_EVENT_NO_ACTIVE_CANDIDATE"
        elif event_rows and sector_rows:
            binding_status = "PARTIAL_MISSING_CAUSAL_PATH"
        else:
            binding_status = "UNBOUND_MISSING_PIT_INPUT"
        return {
            "macro_event_ids": sorted(event_ids),
            "sector_state_ids": sorted(sector_ids),
            "supporting_evidence_claim_ids": claim_ids,
            "missing_claim_ids": missing_claim_ids,
            "resolved_event_ids": sorted(resolved_event_ids),
            "causal_path_ids": sorted(path_ids),
            "causal_edge_ids": sorted(edge_ids),
            "contradiction_ids": sorted(contradiction_ids),
            "binding_status": binding_status,
            "temporal_consistency_status": temporal_status,
            "contradiction_status": contradiction_status,
            "binding_reason": (
                "EXACT_PIT_EVENT_CLAIM_SECTOR_PATH"
                if path_ids and not missing_claim_ids and not rejected_event and not contradiction_ids and pit_inputs_complete and temporal_ok
                else "EXACT_BINDING_REQUIREMENTS_NOT_MET"
            ),
            "pit_inputs_complete": pit_inputs_complete,
        }
