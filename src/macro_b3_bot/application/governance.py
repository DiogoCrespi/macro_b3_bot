"""Small, fail-closed governance primitives for staging and research runs.

This module does not pretend to provide identity authentication.  It records the
identity supplied by the caller and rejects missing/unknown roles; deployment
must bind the actor to an authenticated identity before production use.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any


ROLES = frozenset({"operator", "reviewer", "administrator"})
ROLE_PERMISSIONS = {
    "operator": frozenset({"run_pipeline", "read_audit", "submit_sandbox_order"}),
    "reviewer": frozenset({"read_audit", "review_exposure", "review_hypothesis"}),
    "administrator": frozenset({"read_audit", "review_exposure", "review_hypothesis", "kill_switch"}),
}


@dataclass(frozen=True)
class GovernanceActor:
    actor_id: str
    role: str
    authenticated: bool = False

    def can(self, permission: str) -> bool:
        return bool(self.actor_id and self.authenticated and permission in ROLE_PERMISSIONS.get(self.role, ()))


def require_permission(actor: GovernanceActor, permission: str) -> None:
    if actor.role not in ROLES or not actor.can(permission):
        raise PermissionError(f"governance permission denied: {permission}")


def authenticate_token(token: str, *, actor_id: str, role: str, token_hash: str | None = None) -> GovernanceActor:
    """Authenticate a configured token without storing or logging its plaintext."""
    configured_hash = token_hash or os.environ.get("GOVERNANCE_AUTH_TOKEN_SHA256", "")
    if not token or not actor_id or role not in ROLES or not configured_hash:
        return GovernanceActor(actor_id=actor_id, role=role, authenticated=False)
    presented_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return GovernanceActor(actor_id=actor_id, role=role, authenticated=hmac.compare_digest(presented_hash, configured_hash))


class AppendOnlyAuditLedger:
    """JSONL audit ledger with a hash chain; existing entries are never rewritten."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, *, actor: GovernanceActor, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        require_permission(actor, "read_audit")
        previous_hash = "GENESIS"
        if self.path.exists():
            last = self.path.read_text(encoding="utf-8").splitlines()
            if last:
                previous_hash = json.loads(last[-1])["entry_hash"]
        entry = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "actor_id": actor.actor_id,
            "actor_role": actor.role,
            "action": action,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        entry["entry_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        if not self.path.exists():
            return True, "EMPTY"
        previous = "GENESIS"
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            entry = json.loads(line)
            recorded_hash = entry.pop("entry_hash", None)
            if entry.get("previous_hash") != previous:
                return False, f"BROKEN_CHAIN_LINE_{line_number}"
            canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if recorded_hash != expected:
                return False, f"CHECKSUM_MISMATCH_LINE_{line_number}"
            previous = recorded_hash
        return True, "VALID"


class KillSwitch:
    def __init__(self, path: Path):
        self.path = path

    def activate(self, actor: GovernanceActor, reason: str) -> None:
        require_permission(actor, "kill_switch")
        if not reason.strip():
            raise ValueError("kill switch reason is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"active": True, "reason": reason, "actor_id": actor.actor_id}), encoding="utf-8")

    def is_active(self) -> bool:
        return self.path.exists()
