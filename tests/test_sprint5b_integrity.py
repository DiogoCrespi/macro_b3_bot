import json

from macro_b3_bot.infrastructure.store import DatabaseStore


def test_hypothesis_canonical_payload_is_not_overwritten_by_review(tmp_path):
    store = DatabaseStore(tmp_path / "integrity.duckdb")
    hypothesis = {
        "hypothesis_id": "H-IMMUTABLE",
        "simulation_run_id": "RUN-1",
        "scenario_type": "UNKNOWN",
        "verification_status": "UNVERIFIED",
        "trigger": "IPCA nacional",
    }
    store.save_scenario_hypothesis(hypothesis)
    original = store.connection.execute(
        "SELECT canonical_payload_json FROM scenario_hypotheses WHERE hypothesis_id = ?",
        ["H-IMMUTABLE"],
    ).fetchone()[0]

    store.save_scenario_hypothesis_review({
        "review_id": "REV-1",
        "hypothesis_id": "H-IMMUTABLE",
        "simulation_run_id": "RUN-1",
        "reviewer_type": "DELEGATED_AI_SEMANTIC_REVIEW",
        "reviewed_by": "test",
        "review_decision": "DELEGATED_AI_REJECTED",
        "review_status": "REJECTED_SEMANTIC_SOURCE_MISMATCH",
        "review_confidence": 0.6,
        "fact_review_hash": "hash-1",
    })
    store.connection.commit()

    current = store.connection.execute(
        "SELECT canonical_payload_json FROM scenario_hypotheses WHERE hypothesis_id = ?",
        ["H-IMMUTABLE"],
    ).fetchone()[0]
    review = store.connection.execute(
        "SELECT review_status, review_confidence FROM scenario_hypothesis_reviews WHERE review_id = ?",
        ["REV-1"],
    ).fetchone()
    assert json.loads(current) == json.loads(original)
    assert review == ("REJECTED_SEMANTIC_SOURCE_MISMATCH", 0.6)
    store.close()
