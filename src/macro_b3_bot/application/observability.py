"""Dependency-free operational metrics and alert evaluation for staging."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass
class MetricsRegistry:
    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)

    def inc(self, name: str, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        self.counters[name] = self.counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = float(value)

    def snapshot(self, *, run_id: str, app_version: str) -> dict[str, Any]:
        return {"run_id": run_id, "app_version": app_version, "recorded_at": datetime.now(timezone.utc).isoformat(), "counters": dict(sorted(self.counters.items())), "gauges": dict(sorted(self.gauges.items()))}

    def persist(self, path: Path, *, run_id: str, app_version: str) -> dict[str, Any]:
        snapshot = self.snapshot(run_id=run_id, app_version=app_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return snapshot


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str
    message: str
    metric: str
    value: float | int | None


def evaluate_alerts(snapshot: dict[str, Any]) -> list[Alert]:
    counters = snapshot.get("counters", {})
    gauges = snapshot.get("gauges", {})
    alerts: list[Alert] = []
    if counters.get("sidecar_failures", 0) >= 3:
        alerts.append(Alert("SIDECAR_FAILURE_BURST", "CRITICAL", "sidecar failure threshold reached", "sidecar_failures", counters["sidecar_failures"]))
    if counters.get("pit_late_runs", 0) > 0:
        alerts.append(Alert("PIT_LATE_DATA", "WARN", "point-in-time data arrived late", "pit_late_runs", counters["pit_late_runs"]))
    if counters.get("unresolved_conflicts", 0) > 0:
        alerts.append(Alert("UNRESOLVED_CONFLICT", "WARN", "causal conflict remains unresolved", "unresolved_conflicts", counters["unresolved_conflicts"]))
    if gauges.get("hypothesis_approval_rate", 1.0) < 0.5:
        alerts.append(Alert("LOW_HYPOTHESIS_APPROVAL_RATE", "WARN", "approval rate below staging threshold", "hypothesis_approval_rate", gauges["hypothesis_approval_rate"]))
    return alerts
