from dataclasses import dataclass
from macro_b3_bot.domain.models import MacroEvent


@dataclass(frozen=True, slots=True)
class EventGate:
    novelty_threshold: float = 0.65
    materiality_threshold: float = 0.55

    def should_run_full_pipeline(self, event: MacroEvent) -> bool:
        materiality = 0.6 * event.magnitude_score + 0.4 * event.persistence_score
        return event.novelty_score >= self.novelty_threshold and materiality >= self.materiality_threshold
