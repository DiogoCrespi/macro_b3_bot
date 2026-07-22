from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Callable

from macro_b3_bot.domain.models import OpportunityAssessment


class LegacyFunctionAdapter:
    """Loads one explicitly configured function from a legacy repository."""

    def __init__(self, root: Path, module_name: str, function_name: str):
        self.root = root
        self.module_name = module_name
        self.function_name = function_name
        self._function: Callable[..., Any] | None = None

    def _load(self) -> Callable[..., Any]:
        if self._function is not None:
            return self._function
        if not self.root.exists():
            raise FileNotFoundError(f"legacy repository not found: {self.root}")
        root_str = str(self.root.resolve())
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        module = importlib.import_module(self.module_name)
        function = getattr(module, self.function_name, None)
        if not callable(function):
            raise AttributeError(f"{self.module_name}.{self.function_name} is not callable")
        self._function = function
        return function

    def evaluate(self, assessment: OpportunityAssessment) -> dict[str, Any]:
        function = self._load()
        payload = assessment.model_dump(mode="json")
        result = function(payload)
        if not isinstance(result, dict):
            raise TypeError("legacy function must return dict")
        return result
