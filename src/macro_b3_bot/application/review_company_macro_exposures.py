"""Explicit, identity-bound human review for extracted macro exposure facts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from macro_b3_bot.infrastructure.store import DatabaseStore


class CompanyMacroExposureReviewer:
    def __init__(self, store: DatabaseStore) -> None:
        self.store = store

    def apply_manifest(self, manifest_path: Path) -> dict[str, object]:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        reviewer = str(manifest.get("reviewed_by", "")).strip()
        reviewer_type = manifest.get("reviewer_type")
        if reviewer_type != "HUMAN" or not reviewer:
            raise ValueError("an identified HUMAN reviewer is required")
        decisions = manifest.get("decisions") or []
        if not decisions:
            raise ValueError("review manifest has no decisions")
        reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        approved = rejected = 0
        applied: list[str] = []
        for decision in decisions:
            fact_id = decision["fact_id"]
            expected_hash = decision["source_excerpt_hash"]
            verdict = decision["decision"]
            notes = str(decision.get("notes", "")).strip()
            if verdict not in {"APPROVE", "REJECT"}:
                raise ValueError(f"invalid decision for {fact_id}")
            if len(notes) < 10:
                raise ValueError(f"review notes are required for {fact_id}")
            row = self.store.connection.execute(
                """
                SELECT source_excerpt_hash,evidence_payload
                FROM company_macro_exposure_facts WHERE fact_id=?
                """,
                [fact_id],
            ).fetchone()
            if not row:
                raise ValueError(f"unknown fact_id: {fact_id}")
            if row[0] != expected_hash:
                raise ValueError(f"excerpt hash changed for {fact_id}")
            payload = json.loads(row[1])
            payload["review_confidence"] = 1.0
            payload["confidence"] = round(
                0.35 * payload["extraction_match_confidence"]
                + 0.30 * payload["semantic_scope_confidence"]
                + 0.20 * payload["denominator_confidence"]
                + 0.15,
                4,
            )
            status = "HUMAN_APPROVED" if verdict == "APPROVE" else "HUMAN_REJECTED"
            self.store.connection.execute(
                """
                UPDATE company_macro_exposure_facts
                SET review_status=?,reviewed_by=?,reviewed_at=?,
                    review_decision=?,review_notes=?,evidence_payload=?
                WHERE fact_id=? AND source_excerpt_hash=?
                """,
                [
                    status, reviewer, reviewed_at, verdict, notes,
                    json.dumps(payload, ensure_ascii=False), fact_id, expected_hash,
                ],
            )
            approved += verdict == "APPROVE"
            rejected += verdict == "REJECT"
            applied.append(fact_id)
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        return {
            "manifest": str(manifest_path),
            "manifest_hash": manifest_hash,
            "reviewed_by": reviewer,
            "reviewed_at": reviewed_at.replace(tzinfo=timezone.utc).isoformat(),
            "approved": approved,
            "rejected": rejected,
            "applied_fact_ids": applied,
        }

    def pending_manifest(self, selection_run_id: str) -> dict[str, object]:
        rows = self.store.connection.execute(
            """
            SELECT fact_id,ticker,field_name,source_excerpt_hash,evidence_payload
            FROM company_macro_exposure_facts
            WHERE selection_run_id=? AND review_status='HUMAN_REVIEW_PENDING'
              AND is_active=TRUE
            ORDER BY ticker,field_name
            """,
            [selection_run_id],
        ).fetchall()
        return {
            "selection_run_id": selection_run_id,
            "reviewer_type": "HUMAN",
            "reviewed_by": "",
            "decisions": [
                {
                    "fact_id": fact_id,
                    "ticker": ticker,
                    "field_name": field_name,
                    "source_excerpt_hash": excerpt_hash,
                    "decision": "",
                    "notes": "",
                    "evidence": json.loads(payload),
                }
                for fact_id, ticker, field_name, excerpt_hash, payload in rows
            ],
        }
