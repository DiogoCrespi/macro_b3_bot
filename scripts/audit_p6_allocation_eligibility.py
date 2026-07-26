"""Audit which PIT research decisions are eligible for paper allocation.

This command never approves or mutates decisions.  It produces a truthful
eligibility manifest so the portfolio cannot be unblocked by stale fixtures or
manual JSON edits.
"""

import json
from collections import Counter
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    decisions = json.loads((root / "data/audits/research_4e3_decisions.json").read_text(encoding="utf-8"))
    timing = json.loads((root / "data/audits/research_4f_timing_risk.json").read_text(encoding="utf-8"))
    timing_by_ticker = {item["ticker"]: item for item in timing.get("snapshots", [])}

    rows = []
    eligible = 0
    for item in decisions.get("decisions", []):
        risk = timing_by_ticker.get(item.get("ticker"), {})
        reasons = []
        if item.get("decision") != "WATCH":
            reasons.append(f"DECISION_{item.get('decision', 'MISSING')}")
        if item.get("execution_mode") != "REAL_UPSTREAM_SYNTHESIS":
            reasons.append(f"MODE_{item.get('execution_mode', 'MISSING')}")
        reasons.extend(item.get("critical_blockers", []))
        if risk.get("timing_classification") != "MONITOR":
            reasons.append(f"TIMING_{risk.get('timing_classification', 'MISSING')}")
        if risk.get("risk_classification") in {"HIGH_RISK", "UNACCEPTABLE_RISK"}:
            reasons.append(f"RISK_{risk.get('risk_classification')}")
        is_eligible = not reasons
        eligible += int(is_eligible)
        rows.append({
            "ticker": item.get("ticker"),
            "decision_id": item.get("decision_id"),
            "decision": item.get("decision"),
            "execution_mode": item.get("execution_mode"),
            "critical_blockers": item.get("critical_blockers", []),
            "timing_classification": risk.get("timing_classification"),
            "risk_classification": risk.get("risk_classification"),
            "eligible_for_paper_allocation": is_eligible,
            "reasons": sorted(set(reasons)),
        })

    payload = {
        "phase": "P6",
        "status": "NO_APPROVED_DECISIONS" if eligible == 0 else "ELIGIBLE_DECISIONS_PRESENT",
        "decision_source": "data/audits/research_4e3_decisions.json",
        "timing_source": "data/audits/research_4f_timing_risk.json",
        "total_decisions": len(rows),
        "eligible_for_paper_allocation": eligible,
        "blocker_counts": Counter(reason for row in rows for reason in row["reasons"]),
        "decisions": rows,
        "safety": {"approvals_created": 0, "buy_signals_created": 0, "orders_created": 0},
    }
    out = root / "data/audits/p6_allocation_eligibility.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=dict), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "eligible": eligible, "total": len(rows)}))


if __name__ == "__main__":
    main()
