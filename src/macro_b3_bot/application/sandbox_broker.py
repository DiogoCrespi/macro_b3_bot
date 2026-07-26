"""Deterministic, local-only broker sandbox for Phase 7 staging.

This module deliberately has no network or real-broker integration.  It exists
to prove authorization, idempotency and reconciliation before any production
execution is even considered.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .governance import GovernanceActor, KillSwitch, require_permission


@dataclass(frozen=True)
class SandboxOrder:
    ticker: str
    side: str
    quantity: int
    price: float
    decision_id: str
    actor_id: str
    order_id: str = ""
    status: str = "SANDBOX_ACCEPTED"
    submitted_at: str = ""

    def with_identity(self) -> "SandboxOrder":
        payload = {
            "ticker": self.ticker.upper(),
            "side": self.side.upper(),
            "quantity": self.quantity,
            "price": self.price,
            "decision_id": self.decision_id,
            "actor_id": self.actor_id,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        order_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return SandboxOrder(**payload, order_id=order_id, status=self.status, submitted_at=self.submitted_at)


class SandboxBroker:
    """Append-only local order store with no network side effects."""

    def __init__(self, path: Path, kill_switch: KillSwitch | None = None):
        self.path = path
        self.kill_switch = kill_switch

    def submit(self, order: SandboxOrder, actor: GovernanceActor) -> SandboxOrder:
        require_permission(actor, "submit_sandbox_order")
        if self.kill_switch and self.kill_switch.is_active():
            raise RuntimeError("sandbox kill switch is active")
        if order.quantity <= 0 or order.price <= 0:
            raise ValueError("sandbox order quantity and price must be positive")
        if order.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("sandbox order side must be BUY or SELL")
        normalized = order.with_identity()
        existing = self._read()
        for item in existing:
            if item.get("order_id") == normalized.order_id:
                return SandboxOrder(**item)
        values = asdict(normalized)
        values["submitted_at"] = datetime.now(timezone.utc).isoformat()
        submitted = SandboxOrder(**values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(submitted), sort_keys=True, ensure_ascii=False) + "\n")
        return submitted

    def reconcile(self, expected_order_ids: list[str]) -> dict[str, Any]:
        actual = {item["order_id"] for item in self._read()}
        expected = set(expected_order_ids)
        return {
            "matched": sorted(actual & expected),
            "missing": sorted(expected - actual),
            "extra": sorted(actual - expected),
            "passed": actual == expected,
        }

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
