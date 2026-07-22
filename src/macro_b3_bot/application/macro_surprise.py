from __future__ import annotations

import math
from decimal import Decimal
from datetime import date
from typing import List, Optional
from macro_b3_bot.domain.macro_models import MacroObservation, MarketExpectation, MacroSurprise

class MacroSurpriseDetector:
    """
    Detector deterministico de surpresa macroeconomica (v0).
    Calcula z-score móvel, delta e erro em relacao as expectativas do BCB Focus.
    """
    def __init__(self, min_history_window: int = 5):
        self.min_history_window = min_history_window

    def compute_surprise(
        self,
        current_obs: MacroObservation,
        history: List[MacroObservation],
        expectation: Optional[MarketExpectation] = None
    ) -> MacroSurprise:
        
        indicator = current_obs.indicator
        ref_date = current_obs.reference_date
        current_val = current_obs.value

        previous_obs = history[-1] if history else None
        previous_val = previous_obs.value if previous_obs else None

        delta = (current_val - previous_val) if previous_val is not None else None
        pct_change = ((current_val - previous_val) / previous_val) if (previous_val is not None and previous_val != 0) else None

        # Z-Score móvel
        rolling_z = None
        if len(history) >= self.min_history_window:
            values = [float(h.value) for h in history]
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            std_dev = math.sqrt(variance)

            if std_dev > 0:
                rolling_z = (float(current_val) - mean) / std_dev

        exp_val = expectation.value if expectation else None
        exp_err = (current_val - exp_val) if exp_val is not None else None

        return MacroSurprise(
            indicator=indicator,
            reference_date=ref_date,
            current_value=current_val,
            previous_value=previous_val,
            delta=delta,
            percent_change=pct_change,
            rolling_zscore=rolling_z,
            expectation_value=exp_val,
            expectation_error=exp_err
        )
