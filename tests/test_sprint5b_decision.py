from datetime import datetime, timezone

from macro_b3_bot.application.research_decision_synthesis import ResearchDecisionSynthesizer


AS_OF = datetime(2026, 7, 24, tzinfo=timezone.utc)


def test_unverified_hypothesis_cannot_reach_decision() -> None:
    result = ResearchDecisionSynthesizer().synthesize(
        ticker="MGLU3",
        as_of_timestamp=AS_OF,
        hypotheses=[{
            "hypothesis_id": "H1",
            "verification_status": "UNVERIFIED",
            "binding_status": "BOUND",
            "temporal_consistency_status": "CONSISTENT",
            "contradiction_status": "NO_CONTRADICTION_DETECTED",
        }],
    )
    assert result.decision == "NO_ACTION"
    assert "UNVERIFIED_MIROFISH_HYPOTHESIS" in result.noncritical_warnings
    assert "UNVERIFIED_MIROFISH_HYPOTHESIS" not in result.critical_blockers


def test_verified_bound_hypothesis_does_not_add_hypothesis_blocker() -> None:
    result = ResearchDecisionSynthesizer().synthesize(
        ticker="MGLU3",
        as_of_timestamp=AS_OF,
        hypotheses=[{
            "hypothesis_id": "H1",
            "verification_status": "SUPPORTED",
            "binding_status": "BOUND",
            "temporal_consistency_status": "CONSISTENT",
            "contradiction_status": "NO_CONTRADICTION_DETECTED",
        }],
    )
    assert "UNVERIFIED_MIROFISH_HYPOTHESIS" not in result.critical_blockers
    assert "HYPOTHESIS_BINDING_INVALID" not in result.critical_blockers
