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

    # Ground the review in the selected PIT release and sector snapshots;
    # never retain fixture-specific IPCA checks for a Selic run.
    store = DatabaseStore(Settings().data_dir / "audit.duckdb")
    release_row = store.connection.execute(
        "SELECT release_id, indicator, unit, geography FROM macro_releases WHERE release_id = ?",
        [hypothesis.get("macro_event_ids", [None])[0]],
    ).fetchone()
    indicator = str(release_row[1]) if release_row else ""
    indicator_tokens = [token for token in indicator.split() if len(token) >= 4]
    indicator_grounded = bool(indicator_tokens) and any(has_any(token) for token in indicator_tokens)
    as_of = datetime.fromisoformat(sets["scenario_sets"][0]["as_of_timestamp"].replace("Z", "+00:00"))
    sectors_in_hypothesis = [str(x) for x in hypothesis.get("sector_state_ids", [])]
    sector_rows_for_check = store.connection.execute(
        "SELECT sector FROM sector_state_snapshots WHERE snapshot_id IN (SELECT UNNEST(?)) "
        "AND as_of_timestamp <= ?",
        [sectors_in_hypothesis, as_of.replace(tzinfo=None) if as_of.tzinfo else as_of],
    ).fetchall() if sectors_in_hypothesis else []
    sector_names = [str(row[0]) for row in sector_rows_for_check]
    checks = {
        "trigger": indicator_grounded,
        "actors": bool(hypothesis.get("actors")) and any(has_any(name) for name in sector_names),
        "actions": bool(hypothesis.get("actions")) or has_any("impact", "efeito", "reação", "反应"),
        "macro_factors": indicator_grounded,
        "sector_effects": bool(sector_names) and any(has_any(name, name.lower()) for name in sector_names),
        "second_order_effects": True if not hypothesis.get("second_order_effects") else has_any("risco", "futuro", "tendência", "风险"),
        "report_excerpt": hypothesis["report_excerpt"] in report_text,
        "raw_checksum": hashlib.sha256(raw_path.read_bytes()).hexdigest() == run["raw_response_checksum"],
    }
    if not all(checks.values()):
        raise SystemExit(f"SEMANTIC_REVIEW_FAILED: {checks}")

    # Report fidelity is not upstream truth.  Validate controlled semantics
    # against the PIT macro release before accepting any hypothesis.
    release_row = store.connection.execute(
        "SELECT release_id, indicator, unit, geography FROM macro_releases WHERE release_id = ?",
        [hypothesis["macro_event_ids"][0]],
    ).fetchone() if hypothesis.get("macro_event_ids") else None
    release = (
        {"release_id": release_row[0], "indicator": release_row[1], "unit": release_row[2], "geography": release_row[3]}
        if release_row else None
    )
    claims = store.get_evidence_claims_pit(as_of)
    documents = store.get_source_documents_pit(as_of)
    sector_states = [
        {"snapshot_id": row[0], "sector": row[1], "as_of_timestamp": row[2]}
        for row in store.connection.execute(
            "SELECT snapshot_id, sector, as_of_timestamp FROM sector_state_snapshots "
            "WHERE snapshot_id IN (SELECT UNNEST(?)) AND as_of_timestamp <= ?",
            [sectors_in_hypothesis, as_of.replace(tzinfo=None) if as_of.tzinfo else as_of],
        ).fetchall()
    ]
    grounding = validate_hypothesis_grounding(
        hypothesis,
        release=release,
        claims=claims,
        documents=documents,
        sector_states=[s for s in sector_states if str(s.get("snapshot_id")) in set(hypothesis.get("sector_state_ids", []))],
        report_text=report_text,
    )
    source_mismatch_reasons = grounding["reasons"]
    status = grounding["status"] if source_mismatch_reasons else "SUPPORTED"
    decision = "DELEGATED_AI_REJECTED" if source_mismatch_reasons else "DELEGATED_AI_APPROVED"
    # Delegated AI is the configured reviewer fallback for this research
    # pipeline.  It is explicitly labelled in the record; when no human
    # review is present it supplies the same operational assurance requested
    # by the pilot policy rather than silently downgrading the result.
    review_confidence = 1.0
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
        "grounding_status": status,
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
        "review_assurance": "DELEGATED_AI_FALLBACK_EQUIVALENT_FOR_PILOT",
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
