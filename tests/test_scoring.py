from macro_b3_bot.domain.models import AssetClass, DecisionAction, OpportunityAssessment
from macro_b3_bot.domain.scoring import compute_score, decide


def good_assessment() -> OpportunityAssessment:
    return OpportunityAssessment(
        ticker="TEST3",
        asset_class=AssetClass.STOCK,
        event_id="evt",
        evidence_quality=0.90,
        scenario_probability=0.80,
        causal_strength=0.90,
        company_exposure=0.85,
        fundamental_quality=0.80,
        valuation_attractiveness=0.80,
        entry_timing=0.75,
        portfolio_fit=0.80,
        confidence=0.80,
        expected_upside=0.30,
        expected_downside=-0.12,
        independent_evidence_count=4,
        has_primary_source=True,
    )


def test_score_is_bounded() -> None:
    score = compute_score(good_assessment())
    assert 0 <= score <= 1


def test_buy_requires_all_gates() -> None:
    result = decide(
        good_assessment(),
        min_score=0.72,
        min_confidence=0.65,
        min_reward_risk=1.8,
        min_evidence=3,
    )
    assert result.action == DecisionAction.BUY


def test_youtube_only_narrative_is_not_buy() -> None:
    item = good_assessment().model_copy(
        update={
            "has_primary_source": False,
            "independent_evidence_count": 1,
            "penalties": {"youtube_only": 0.20},
        }
    )
    result = decide(item, min_score=0.72, min_confidence=0.65, min_reward_risk=1.8, min_evidence=3)
    assert result.action != DecisionAction.BUY
