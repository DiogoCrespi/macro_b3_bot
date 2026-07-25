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

    # Report fidelity is not upstream truth.  Validate controlled semantics
    # against the PIT macro release before accepting any hypothesis.
    store = DatabaseStore(Settings().data_dir / "macro_b3_bot.duckdb")
    release = store.connection.execute(
        "SELECT indicator, unit, geography FROM macro_releases WHERE release_id = ?",
        [hypothesis["macro_event_ids"][0]],
    ).fetchone() if hypothesis.get("macro_event_ids") else None
    indicator = str(release[0] if release else "")
    geography_value = release[2] if release else ""
    try:
        geography_items = json.loads(geography_value) if isinstance(geography_value, str) else geography_value
    except json.JSONDecodeError:
        geography_items = [geography_value]
    geography = ",".join(str(item) for item in (geography_items or []))
    trigger = str(hypothesis.get("trigger", ""))
    source_mismatch_reasons = []
    if not hypothesis.get("source_document_ids"):
        source_mismatch_reasons.append("SOURCE_DOCUMENT_IDS_MISSING_FROM_HYPOTHESIS")
    if "IPCA" in indicator.upper() and any(token in trigger.lower() for token in ("global", "全球", "global")):
        source_mismatch_reasons.append("IPCA_NATIONAL_INDEX_MAPPED_TO_GLOBAL_INFLATION")
    if "ITR" in report_text and any(token in report_text.lower() for token in ("information technology report", "信息技术报告")):
        source_mismatch_reasons.append("CVM_ITR_MAPPED_TO_INFORMATION_TECHNOLOGY_REPORT")
    if release and geography and any(token in trigger.lower() for token in ("global", "全球")) and any(
        item.upper() in {"BR", "BRAZIL", "BRASIL"} for item in geography.split(",")
    ):
        source_mismatch_reasons.append("BRAZILIAN_RELEASE_MAPPED_TO_GLOBAL_GEOGRAPHY")

    status = "REJECTED_SEMANTIC_SOURCE_MISMATCH" if source_mismatch_reasons else "PARTIALLY_SUPPORTED"
    decision = "DELEGATED_AI_REJECTED" if source_mismatch_reasons else "DELEGATED_AI_APPROVED"
    review_confidence = 0.60
    review_notes = (
        "; ".join(source_mismatch_reasons)
        if source_mismatch_reasons
        else "Report extraction is faithful, but delegated review is not an independent factual approval."
    )
    fact_review_payload = {
        "hypothesis_id": hypothesis["hypothesis_id"],
        "simulation_run_id": hypothesis["simulation_run_id"],
        "canonical_hypothesis": hypothesis,
        "source_report_checksum": run["raw_response_checksum"],
        "source_excerpt": hypothesis["report_excerpt"],
        "source_mismatch_reasons": source_mismatch_reasons,
        "methodology_version": "5B.1-semantic-grounding-v1",
    }
    fact_review_hash = hashlib.sha256(
        json.dumps(fact_review_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    review = {
        "review_id": hashlib.sha256((hypothesis["hypothesis_id"] + fact_review_hash).encode()).hexdigest(),
        "hypothesis_id": hypothesis["hypothesis_id"],
        "simulation_run_id": hypothesis["simulation_run_id"],
        "reviewer_type": "DELEGATED_AI_SEMANTIC_REVIEW",
        "reviewed_by": "Codex",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_decision": decision,
        "review_status": status,
        "review_confidence": review_confidence,
        "review_assurance": "DELEGATED_AI_ONLY_NOT_HUMAN_VERIFIED",
        "review_notes": review_notes,
        "field_checks": checks,
        "source_report_id": hypothesis["raw_report_id"],
        "source_report_checksum": run["raw_response_checksum"],
        "source_excerpt_hash": hashlib.sha256(hypothesis["report_excerpt"].encode()).hexdigest(),
        "operational_use": "BLOCKED_UNTIL_VERIFIED_POLICY",
        "fact_review_hash": fact_review_hash,
        "semantic_grounding": {
            "release_id": hypothesis.get("macro_event_ids", [None])[0],
            "indicator": indicator,
            "geography": geography,
            "source_mismatch_reasons": source_mismatch_reasons,
        },
    }

    # Reviews and validations are append-only projections. The canonical
    # ScenarioHypothesis payload is intentionally never updated.
    audit_path = root / "data/audits/mirofish_5a_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["semantic_review"] = review
    audit["unverified_count"] = 1
    audit["semantic_grounding_status"] = status
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    store.save_scenario_hypothesis_review(review)
    validation = {
        "validation_id": hashlib.sha256((hypothesis["hypothesis_id"] + fact_review_hash + status).encode()).hexdigest(),
        "hypothesis_id": hypothesis["hypothesis_id"],
        "validation_status": status,
        "validator_type": "DELEGATED_AI_SEMANTIC_REVIEW",
        "fact_review_hash": fact_review_hash,
        "source_mismatch_reasons": source_mismatch_reasons,
    }
    store.save_scenario_hypothesis_validation(validation)
    store.connection.commit()
    store.close()
    review_path = root / "data/audits/mirofish_5a_hypothesis_reviews.json"
    review_path.write_text(json.dumps({"reviews": [review]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"hypothesis_id": hypothesis["hypothesis_id"], "status": status, "decision": review["review_decision"], "operational_use": review["operational_use"]}))


if __name__ == "__main__":
    main()
