from macro_b3_bot.application.event_gate import EventGate
from macro_b3_bot.domain.models import MacroEvent


def test_event_gate_blocks_small_event() -> None:
    event = MacroEvent(
        event_id="x",
        title="small event",
        event_type="test",
        novelty_score=0.30,
        magnitude_score=0.20,
        persistence_score=0.20,
        evidence=[],
    )
    assert EventGate().should_run_full_pipeline(event) is False
