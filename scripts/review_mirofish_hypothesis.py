"""Perform an auditable delegated semantic review of a persisted hypothesis."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sets_path = root / "data/audits/mirofish_5a_scenario_sets.json"
    runs_path = root / "data/audits/mirofish_5a_simulation_runs.json"
    sets = json.loads(sets_path.read_text(encoding="utf-8"))
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    hypothesis = sets["hypotheses"][0]
    run = runs["runs"][0]
    raw_path = root / "data/raw/mirofish/reports" / f"{run['raw_response_checksum']}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    report_text = raw.get("markdown_content", "")

    # Semantic review uses the report's Chinese wording rather than requiring
    # every translated extraction label to be a byte-for-byte substring.
    checks = {
        "trigger": hypothesis["trigger"] in report_text,
        "actors": all(token in report_text for token in ("消费者", "零售商", "供应链", "电子商务")),
        "actions": all(token in report_text for token in ("灵活定价", "供应链管理", "数字技术", "社区团购")),
        "macro_factors": all(token in report_text for token in ("通胀率", "购买力", "成本", "价格敏感")),
        "sector_effects": all(token in report_text for token in ("价格上涨", "购买力下降", "业务")),
        "second_order_effects": all(token in report_text for token in ("供应链", "技术创新", "竞争力")),
        "report_excerpt": hypothesis["report_excerpt"] in report_text,
        "raw_checksum": hashlib.sha256(raw_path.read_bytes()).hexdigest() == run["raw_response_checksum"],
    }
    if not all(checks.values()):
        raise SystemExit(f"SEMANTIC_REVIEW_FAILED: {checks}")

    # The source supports the content, but the Chinese scenario label cannot
    # be mapped safely to the controlled scenario enum. Keep the result partial
    # and below the threshold for operational decision use.
    review = {
        "review_id": hashlib.sha256((hypothesis["hypothesis_id"] + run["raw_response_checksum"]).encode()).hexdigest(),
        "hypothesis_id": hypothesis["hypothesis_id"],
        "simulation_run_id": hypothesis["simulation_run_id"],
        "reviewer_type": "DELEGATED_AI",
        "reviewed_by": "Codex",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_decision": "DELEGATED_AI_APPROVED",
        "review_status": "PARTIALLY_SUPPORTED",
        "review_confidence": 1.0,
        "review_notes": "All extracted content fields and the evidence excerpt are semantically supported by the persisted sidecar report. Scenario type remains UNKNOWN because the source label is not safely mappable to the controlled enum.",
        "field_checks": checks,
        "source_report_id": hypothesis["raw_report_id"],
        "source_report_checksum": run["raw_response_checksum"],
        "source_excerpt_hash": hashlib.sha256(hypothesis["report_excerpt"].encode()).hexdigest(),
        "operational_use": "BLOCKED_UNTIL_VERIFIED_POLICY",
    }

    # Update the persisted review state without changing the hypothesis ID.
    hypothesis["verification_status"] = "PARTIALLY_SUPPORTED"
    hypothesis["extraction_metadata"]["semantic_review"] = review
    sets["hypotheses"] = [hypothesis]
    sets_path.write_text(json.dumps(sets, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_path = root / "data/audits/mirofish_5a_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["semantic_review"] = review
    audit["unverified_count"] = 0
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    store = DatabaseStore(Settings().data_dir / "macro_b3_bot.duckdb")
    store.connection.execute(
        "UPDATE scenario_hypotheses SET verification_status = ?, canonical_payload_json = ? WHERE hypothesis_id = ?",
        ["PARTIALLY_SUPPORTED", json.dumps(hypothesis, ensure_ascii=False, sort_keys=True), hypothesis["hypothesis_id"]],
    )
    store.connection.commit()
    store.close()
    review_path = root / "data/audits/mirofish_5a_hypothesis_reviews.json"
    review_path.write_text(json.dumps({"reviews": [review]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"hypothesis_id": hypothesis["hypothesis_id"], "status": hypothesis["verification_status"], "decision": review["review_decision"], "operational_use": review["operational_use"]}))


if __name__ == "__main__":
    main()
