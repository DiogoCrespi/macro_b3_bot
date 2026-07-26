"""Compute auditable P4 decision metrics and MiroFish ablation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _max_drawdown(nav: list[float]) -> float | None:
    if not nav:
        return None
    peak = nav[0]
    worst = 0.0
    for value in nav:
        peak = max(peak, value)
        worst = min(worst, (value / peak) - 1 if peak else 0.0)
    return abs(worst)


def _metrics(replay: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    steps = replay.get("steps", [])
    nav = [float(row["nav"]) for row in steps if row.get("nav") is not None]
    events = ledger.get("events", [])
    evaluated = [row for row in events if row.get("event_type") != "NO_ALLOCATION"]
    outcomes = [row for row in evaluated if row.get("outcome_status") in {"WIN", "LOSS"}]
    gross = sum(float(row.get("gross_value") or 0) for row in events)
    costs = sum(float(row.get("transaction_cost") or 0) + float(row.get("slippage_cost") or 0) for row in events)
    final_nav = nav[-1] if nav else None
    initial_nav = nav[0] if nav else None
    return {
        "precision_at_k": None if not outcomes else sum(row["outcome_status"] == "WIN" for row in outcomes) / len(outcomes),
        "hit_rate": None if not outcomes else sum(row["outcome_status"] == "WIN" for row in outcomes) / len(outcomes),
        "metric_status": "NOT_EVALUABLE_NO_ALLOCATED_OUTCOMES" if not outcomes else "CALCULATED",
        "outcome_count": len(outcomes),
        "max_drawdown": _max_drawdown(nav),
        "turnover_ratio": (gross / initial_nav) if initial_nav else None,
        "gross_traded_brl": gross,
        "transaction_and_slippage_costs_brl": costs,
        "return_pct": ((final_nav / initial_nav) - 1) if initial_nav else None,
        "steps": len(steps),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    replay = json.loads((root / "data/audits/replay_4g_end_to_end.json").read_text(encoding="utf-8"))
    ledger = json.loads((root / "data/audits/paper_portfolio_4g_ledger.json").read_text(encoding="utf-8"))
    decisions = json.loads((root / "data/audits/research_4e3_decisions.json").read_text(encoding="utf-8"))
    deterministic = {item["ticker"]: item["decision"] for item in decisions["decisions"]}
    # No supported/bound MiroFish hypothesis existed at the historical replay
    # cutoffs, so the second arm must remain observationally identical rather
    # than manufacturing an effect.
    ablation = {
        "DETERMINISTIC_ONLY": deterministic,
        "DETERMINISTIC_PLUS_MIROFISH": deterministic,
        "difference_count": 0,
        "status": "NO_MIROFISH_SUPPORTED_AT_HISTORICAL_CUTOFFS",
    }
    result = {
        "sprint": "P4",
        "methodology_version": "P4-decision-metrics-ablation-v1",
        "replay_run_id": replay["replay_run"]["replay_run_id"],
        "metrics": _metrics(replay, ledger),
        "ablation": ablation,
        "precision_hit_rate_status": "BLOCKED_NO_EVALUABLE_ALLOCATIONS",
        "drawdown_turnover_costs_status": "CALCULATED_FROM_REPLAY",
        "promotion_status": "NOT_PROMOTED_TO_DECISION_POLICY",
        "source_manifests": [
            "data/audits/replay_4g_end_to_end.json",
            "data/audits/paper_portfolio_4g_ledger.json",
            "data/audits/research_4e3_decisions.json",
        ],
    }
    result["report_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    (root / "data/audits/p4_decision_metrics_ablation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
