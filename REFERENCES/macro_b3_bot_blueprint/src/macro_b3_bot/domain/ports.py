from pathlib import Path
from typing import Protocol
from .models import AssetSnapshot, MacroEvent, OpportunityAssessment, ScenarioSet


class AssetUniversePort(Protocol):
    def load_assets(self) -> list[AssetSnapshot]: ...


class ScenarioEnginePort(Protocol):
    def run(self, event: MacroEvent, seed_document: Path) -> ScenarioSet: ...


class LegacyTribunalPort(Protocol):
    def evaluate(self, assessment: OpportunityAssessment) -> dict: ...


class LegacyRiskPort(Protocol):
    def evaluate(self, assessment: OpportunityAssessment) -> dict: ...
