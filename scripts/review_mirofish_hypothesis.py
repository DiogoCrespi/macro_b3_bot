"""Perform an auditable delegated semantic review of a persisted hypothesis."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macro_b3_bot.config import Settings
from macro_b3_bot.application.semantic_grounding import validate_hypothesis_grounding
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

    # Semantic review checks concepts in the persisted report without assuming
    # one report language. Extraction labels may be translated, but the
    # report itself must retain the Brazilian indicator, retail state, and
    # source meaning.
    def has_any(*terms: str) -> bool:
        folded = report_text.casefold()
        return any(term.casefold() in folded for term in terms)

    checks = {
        "trigger": has_any("IPCA", "aceleração da taxa de inflação", "通胀"),
        "actors": has_any("consum", "消费者") and has_any("varej", "零售"),
        "actions": has_any("preço", "价格") and has_any("cadeia", "供应链"),
        "macro_factors": has_any("IPCA", "IPC") and has_any("infla", "通胀"),
        "sector_effects": has_any("varej", "零售") and has_any("preço", "价格"),
        "second_order_effects": has_any("cadeia", "供应链") or has_any("consum", "消费者"),
        "report_excerpt": hypothesis["report_excerpt"] in report_text,
        "raw_checksum": hashlib.sha256(raw_path.read_bytes()).hexdigest() == run["raw_response_checksum"],
    }
    if not all(checks.values()):
        raise SystemExit(f"SEMANTIC_REVIEW_FAILED: {checks}")

    # Report fidelity is not upstream truth.  Validate controlled semantics
    # against the PIT macro release before accepting any hypothesis.
    store = DatabaseStore(Settings().data_dir / "audit.duckdb")
    release_row = store.connection.execute(
        "SELECT release_id, indicator, unit, geography FROM macro_releases WHERE release_id = ?",
        [hypothesis["macro_event_ids"][0]],
    ).fetchone() if hypothesis.get("macro_event_ids") else None
    release = (
        {"release_id": release_row[0], "indicator": release_row[1], "unit": release_row[2], "geography": release_row[3]}
        if release_row else None
    )
    as_of = datetime.fromisoformat(sets["scenario_sets"][0]["as_of_timestamp"].replace("Z", "+00:00"))
    claims = store.get_evidence_claims_pit(as_of)
    documents = store.get_source_documents_pit(as_of)
    sector_states = store.get_sector_state_snapshots_pit(as_of)
    grounding = validate_hypothesis_grounding(
        hypothesis,
        release=release,
        claims=claims,
        documents=documents,
        sector_states=[s for s in sector_states if str(s.get("snapshot_id")) in set(hypothesis.get("sector_state_ids", []))],
        report_text=report_text,
    )
    source_mismatch_reasons = grounding["reasons"]
    status = grounding["status"] if source_mismatch_reasons else "PARTIALLY_SUPPORTED"
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
        "semantic_grounding": {"release_id": hypothesis.get("macro_event_ids", [None])[0], **grounding},
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
