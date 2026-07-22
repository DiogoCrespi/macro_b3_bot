from dataclasses import dataclass
from macro_b3_bot.config import Settings
from macro_b3_bot.domain.models import InvestmentDecision, OpportunityAssessment
from macro_b3_bot.domain.scoring import decide


@dataclass(slots=True)
class DecisionPipeline:
    settings: Settings

    def evaluate(self, assessments: list[OpportunityAssessment]) -> list[InvestmentDecision]:
        decisions = [
            decide(
                item,
                min_score=self.settings.min_score_buy,
                min_confidence=self.settings.min_confidence_buy,
                min_reward_risk=self.settings.min_reward_risk,
                min_evidence=self.settings.min_independent_evidence,
            )
            for item in assessments
        ]
        return sorted(decisions, key=lambda item: item.score, reverse=True)
