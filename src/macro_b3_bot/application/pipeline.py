from dataclasses import dataclass
from macro_b3_bot.config import Settings
from macro_b3_bot.domain.models import InvestmentDecision, OpportunityAssessment, DecisionAction
from macro_b3_bot.domain.scoring import decide


@dataclass(slots=True)
class DecisionPipeline:
    settings: Settings

    def evaluate(self, assessments: list[OpportunityAssessment]) -> list[InvestmentDecision]:
        decisions: list[InvestmentDecision] = []
        for item in assessments:
            decision = decide(
                item,
                min_score=self.settings.min_score_buy,
                min_confidence=self.settings.min_confidence_buy,
                min_reward_risk=self.settings.min_reward_risk,
                min_evidence=self.settings.min_independent_evidence,
            )
            # Trava de Segurança de Pesquisa (Research Mode Gate)
            if self.settings.research_mode or not self.settings.allow_buy_signals:
                if decision.action == DecisionAction.BUY:
                    decision.action = DecisionAction.WATCH
                    decision.max_position_pct = 0.0
                    decision.reasons.append("BUY blocked: research mode")
            decisions.append(decision)
            
        return sorted(decisions, key=lambda item: item.score, reverse=True)
