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
        self._ensure_schema()

    @staticmethod
    def fact_review_hash(
        fact_id: str,
        ticker: str,
        field_name: str,
        normalized_value: str,
        evidence_payload: str | dict[str, object],
        methodology_version: str,
    ) -> str:
        payload = (
            json.loads(evidence_payload)
            if isinstance(evidence_payload, str)
            else evidence_payload
        )
        reviewed_content = {
            "fact_id": fact_id,
            "ticker": ticker,
            "field_name": field_name,
            "normalized_value": json.loads(normalized_value),
            "document_id": payload.get("evidence_id"),
            "document_version": payload.get("document_version"),
            "document_checksum": payload.get("document_checksum"),
            "section_name": payload.get("section_name"),
            "section_checksum": payload.get("section_checksum"),
            "source_filename": payload.get("source_filename"),
            "evidence_excerpt": payload.get("evidence_excerpt"),
            "scope_type": payload.get("scope_type"),
            "scope_period": payload.get("scope_period"),
            "denominator_basis": payload.get("denominator_basis"),
            "formula": payload.get("formula"),
            "derivation_components": payload.get("derivation_components"),
            "methodology_version": methodology_version,
        }
        canonical = json.dumps(
            reviewed_content, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def apply_manifest(
        self,
        manifest_path: Path,
        *,
        confirmed_identity: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, object]:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        selection_run_id = str(manifest.get("selection_run_id", "")).strip()
        reviewer = str(manifest.get("reviewed_by", "")).strip()
        reviewer_type = manifest.get("reviewer_type")
        if not selection_run_id:
            raise ValueError("selection_run_id is required")
        if (
            reviewer_type != "HUMAN"
            or not reviewer
            or not confirmed
            or confirmed_identity != reviewer
        ):
            raise ValueError(
                "an explicitly confirmed reviewer identity is required; "
                "this is an identity assertion, not cryptographic authentication"
            )
        decisions = manifest.get("decisions") or []
        if not decisions:
            raise ValueError("review manifest has no decisions")
        reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        approved = rejected = 0
        applied: list[str] = []
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        prepared: list[tuple[dict[str, object], tuple, dict[str, object]]] = []
        self.store.connection.execute("BEGIN TRANSACTION")
        try:
            for decision in decisions:
                fact_id = decision["fact_id"]
                expected_hash = decision["fact_review_hash"]
                verdict = decision["decision"]
                notes = str(decision.get("notes", "")).strip()
                if verdict not in {"APPROVE", "REJECT"}:
                    raise ValueError(f"invalid decision for {fact_id}")
                if len(notes) < 10:
                    raise ValueError(f"review notes are required for {fact_id}")
                row = self.store.connection.execute(
                    """
                    SELECT ticker,field_name,normalized_value,evidence_payload,
                           methodology_version,fact_review_hash
                      FROM company_macro_exposure_facts
                     WHERE fact_id=? AND selection_run_id=? AND is_active=TRUE
                       AND review_status='HUMAN_REVIEW_PENDING'
                    """,
                    [fact_id, selection_run_id],
                ).fetchone()
                if not row:
                    raise ValueError(f"fact is not pending in manifest run: {fact_id}")
                current_hash = self.fact_review_hash(
                    fact_id, row[0], row[1], row[2], row[3], row[4]
                )
                if row[5] != current_hash or expected_hash != current_hash:
                    raise ValueError(f"reviewed fact content changed for {fact_id}")
                payload = json.loads(row[3])
                payload["review_confidence"] = 1.0
                payload["confidence"] = round(
                    0.35 * payload["extraction_match_confidence"]
                    + 0.30 * payload["semantic_scope_confidence"]
                    + 0.20 * payload["denominator_confidence"]
                    + 0.15,
                    4,
                )
                prepared.append((decision, row, payload))

            for decision, row, payload in prepared:
                fact_id = decision["fact_id"]
                verdict = decision["decision"]
                notes = str(decision["notes"]).strip()
                status = (
                    "HUMAN_APPROVED" if verdict == "APPROVE"
                    else "HUMAN_REJECTED"
                )
                updated = self.store.connection.execute(
                    """
                    UPDATE company_macro_exposure_facts
                       SET review_status=?,reviewed_by=?,reviewed_at=?,
                           review_decision=?,review_notes=?,evidence_payload=?
                     WHERE fact_id=? AND selection_run_id=? AND is_active=TRUE
                       AND review_status='HUMAN_REVIEW_PENDING'
                       AND fact_review_hash=?
                    RETURNING fact_id
                    """,
                    [
                        status, reviewer, reviewed_at, verdict, notes,
                        json.dumps(payload, ensure_ascii=False), fact_id,
                        selection_run_id, decision["fact_review_hash"],
                    ],
                ).fetchone()
                if not updated:
                    raise ValueError(f"concurrent review change for {fact_id}")
                log_id = hashlib.sha256(
                    f"{manifest_hash}|{fact_id}|{verdict}".encode()
                ).hexdigest()[:32]
                self.store.connection.execute(
                    """
                    INSERT INTO company_exposure_review_log (
                        log_id,manifest_hash,selection_run_id,fact_id,
                        fact_review_hash,decision,reviewer_assertion,
                        confirmed_identity,reviewed_at,notes
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        log_id, manifest_hash, selection_run_id, fact_id,
                        decision["fact_review_hash"], verdict, reviewer,
                        confirmed_identity, reviewed_at, notes,
                    ],
                )
                approved += verdict == "APPROVE"
                rejected += verdict == "REJECT"
                applied.append(fact_id)
            self.store.connection.execute("COMMIT")
        except Exception:
            self.store.connection.execute("ROLLBACK")
            raise
        return {
            "manifest": str(manifest_path),
            "manifest_hash": manifest_hash,
            "reviewed_by": reviewer,
            "identity_assurance": "LOCAL_CONFIRMED_ASSERTION_NOT_CRYPTOGRAPHIC",
            "reviewed_at": reviewed_at.replace(tzinfo=timezone.utc).isoformat(),
            "approved": approved,
            "rejected": rejected,
            "applied_fact_ids": applied,
        }

    def pending_manifest(self, selection_run_id: str) -> dict[str, object]:
        rows = self.store.connection.execute(
            """
            SELECT fact_id,ticker,field_name,fact_review_hash,evidence_payload
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
                    "fact_review_hash": review_hash,
                    "decision": "",
                    "notes": "",
                    "evidence": json.loads(payload),
                }
                for fact_id, ticker, field_name, review_hash, payload in rows
            ],
            "identity_assurance": (
                "Reviewer identity is locally confirmed by CLI assertion; "
                "it is not cryptographically authenticated."
            ),
        }

    def _ensure_schema(self) -> None:
        columns = {
            row[1] for row in self.store.connection.execute(
                "PRAGMA table_info('company_macro_exposure_facts')"
            ).fetchall()
        }
        if "fact_review_hash" not in columns:
            self.store.connection.execute(
                "ALTER TABLE company_macro_exposure_facts "
                "ADD COLUMN fact_review_hash VARCHAR"
            )
        rows = self.store.connection.execute(
            """
            SELECT fact_id,ticker,field_name,normalized_value,evidence_payload,
                   methodology_version
              FROM company_macro_exposure_facts
             WHERE fact_review_hash IS NULL
            """
        ).fetchall()
        for row in rows:
            review_hash = self.fact_review_hash(*row)
            self.store.connection.execute(
                "UPDATE company_macro_exposure_facts SET fact_review_hash=? "
                "WHERE fact_id=?",
                [review_hash, row[0]],
            )
        self.store.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS company_exposure_review_log (
                log_id VARCHAR PRIMARY KEY,
                manifest_hash VARCHAR NOT NULL,
                selection_run_id VARCHAR NOT NULL,
                fact_id VARCHAR NOT NULL,
                fact_review_hash VARCHAR NOT NULL,
                decision VARCHAR NOT NULL,
                reviewer_assertion VARCHAR NOT NULL,
                confirmed_identity VARCHAR NOT NULL,
                reviewed_at TIMESTAMP NOT NULL,
                notes VARCHAR NOT NULL
            )
            """
        )
