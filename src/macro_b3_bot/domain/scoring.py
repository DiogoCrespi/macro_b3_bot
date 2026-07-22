from .models import DecisionAction, InvestmentDecision, OpportunityAssessment


WEIGHTS = {
    "evidence_quality": 0.18,
    "scenario_probability": 0.17,
    "causal_strength": 0.18,
    "company_exposure": 0.15,
    "fundamental_quality": 0.10,
    "valuation_attractiveness": 0.08,
    "entry_timing": 0.07,
    "portfolio_fit": 0.07,
}


def compute_score(item: OpportunityAssessment) -> float:
    gross = sum(getattr(item, field) * weight for field, weight in WEIGHTS.items())
    penalty = sum(max(0.0, value) for value in item.penalties.values())
    return max(0.0, min(1.0, gross - penalty))


def decide(
    item: OpportunityAssessment,
    *,
    min_score: float,
    min_confidence: float,
    min_reward_risk: float,
    min_evidence: int,
) -> InvestmentDecision:
    score = compute_score(item)
    rr = item.reward_risk
    blockers: list[str] = []

    if item.risk_veto:
        blockers.append("risk veto")
    if item.skeptic_veto:
        blockers.append("skeptic veto")
    if item.stale_critical_data:
        blockers.append("critical data is stale")
    if item.independent_evidence_count < min_evidence:
        blockers.append("insufficient independent evidence")
    if not item.has_primary_source:
        blockers.append("no primary source")
    if rr is None or rr < min_reward_risk:
        blockers.append("reward/risk below threshold")

    eligible = (
        score >= min_score
        and item.confidence >= min_confidence
        and not blockers
    )

    if eligible:
        action = DecisionAction.BUY
        reasons = item.thesis or ["all decision gates passed"]
        max_position = min(0.10, 0.02 + 0.08 * score * item.confidence)
    elif score >= 0.60 and not item.risk_veto:
        action = DecisionAction.WATCH
        reasons = (item.thesis or ["partial thesis support"]) + blockers
        max_position = 0.0
    else:
        action = DecisionAction.NO_ACTION
        reasons = blockers or ["score below action threshold"]
        max_position = 0.0

    return InvestmentDecision(
        ticker=item.ticker,
        action=action,
        score=round(score, 4),
        confidence=item.confidence,
        reasons=reasons,
        invalidators=item.invalidators,
        reward_risk=None if rr is None else round(rr, 3),
        max_position_pct=round(max_position, 4),
    )
