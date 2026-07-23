"""
Macro surprise detection — Sprint 4A.

Computes z-score-based surprise for each MacroRelease against:
  1. Historical deviation from consensus (when available)
  2. Historical distribution of period-over-period changes (fallback)

Formula:
  With consensus:    z = (actual - consensus) / σ_errors
  Without consensus: z = (Δ_actual - μ_Δ) / σ_Δ
  surprise_score = min(1.0, abs(z) / z_cap)
"""
from __future__ import annotations

import logging
import statistics
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

_Z_SCORE_CAP = 4.0
_MIN_PERIODS = 12          # minimum observations for z-score
_WEIGHT_CONSENSUS = 0.70
_WEIGHT_HISTORY = 0.30


def compute_surprise_score(
    actual: Decimal,
    consensus: Optional[Decimal],
    historical_values: list[Decimal],
    historical_consensus_errors: Optional[list[Decimal]] = None,
) -> tuple[float, dict]:
    """
    Compute the surprise score for a single release observation.

    Parameters
    ----------
    actual                   : the release value
    consensus                : market consensus at release time (or None)
    historical_values        : list of prior actual_values, most recent last
    historical_consensus_errors : list of (actual - consensus) for prior periods (optional)

    Returns
    -------
    (surprise_score, breakdown_dict)
    """
    breakdown: dict = {}
    n = len(historical_values)

    # ── Path A: Consensus-based ────────────────────────────────────────────
    if consensus is not None and historical_consensus_errors and len(historical_consensus_errors) >= _MIN_PERIODS:
        raw_errors = [float(e) for e in historical_consensus_errors]
        mu_err = statistics.mean(raw_errors)
        sigma_err = statistics.stdev(raw_errors) if len(raw_errors) > 1 else 1.0
        if sigma_err == 0:
            sigma_err = 1.0

        current_error = float(actual - consensus)
        z_consensus = (current_error - mu_err) / sigma_err
        score_consensus = min(1.0, abs(z_consensus) / _Z_SCORE_CAP)

        breakdown["consensus_z"] = round(z_consensus, 4)
        breakdown["score_consensus"] = round(score_consensus, 4)

        # ── Path B (blended): history fallback ────────────────────────────
        score_history, hist_breakdown = _historical_zscore(actual, historical_values)
        breakdown.update(hist_breakdown)

        final_score = _WEIGHT_CONSENSUS * score_consensus + _WEIGHT_HISTORY * score_history
        breakdown["method"] = "CONSENSUS_BLENDED"

    # ── Path B: Historical deviation only ─────────────────────────────────
    else:
        final_score, hist_breakdown = _historical_zscore(actual, historical_values)
        breakdown.update(hist_breakdown)

        if n < _MIN_PERIODS:
            # Penalise short history
            final_score *= max(0.5, n / _MIN_PERIODS)
            breakdown["short_history_penalty"] = True
        breakdown["method"] = "HISTORY_ONLY"

    breakdown["n_observations"] = n
    breakdown["actual"] = float(actual)
    if consensus is not None:
        breakdown["consensus"] = float(consensus)

    return round(final_score, 4), breakdown


def _historical_zscore(actual: Decimal, historical_values: list[Decimal]) -> tuple[float, dict]:
    """
    Compute z-score based on period-over-period changes (Δ distribution).
    Returns (score, breakdown).
    """
    if len(historical_values) < 2:
        return 0.0, {"hist_z": None, "score_history": 0.0}

    deltas = [float(historical_values[i] - historical_values[i - 1]) for i in range(1, len(historical_values))]
    mu_delta = statistics.mean(deltas)
    sigma_delta = statistics.stdev(deltas) if len(deltas) > 1 else 1.0
    if sigma_delta == 0:
        sigma_delta = 1.0

    # Current delta: actual vs most recent historical
    current_delta = float(actual - historical_values[-1])
    z = (current_delta - mu_delta) / sigma_delta
    score = min(1.0, abs(z) / _Z_SCORE_CAP)

    return round(score, 4), {
        "hist_z": round(z, 4),
        "mu_delta": round(mu_delta, 6),
        "sigma_delta": round(sigma_delta, 6),
        "current_delta": round(current_delta, 6),
        "score_history": round(score, 4),
    }


def compute_novelty_score(
    event_type: str,
    series_code: str,
    current_score: float,
    days_since_last_event: Optional[int],
    magnitude_percentile: float,
    combination_rarity: float,
    recent_event_count_30d: int,
) -> tuple[float, dict]:
    """
    Compute novelty score from four weighted components.

    Parameters
    ----------
    days_since_last_event   : calendar days since last event of same type/series
    magnitude_percentile    : percentile of current |surprise| in historical distribution (0–1)
    combination_rarity      : how unusual this multi-variable combination is (0–1)
    recent_event_count_30d  : number of similar events in last 30 days
    """
    w_time = 0.40
    w_magnitude = 0.30
    w_combination = 0.20
    w_absence = 0.10

    # Time component: 0 if < 30 days, linear up to 1.0 at 90+ days
    if days_since_last_event is None:
        time_score = 1.0   # never seen before
    elif days_since_last_event < 30:
        time_score = days_since_last_event / 30.0 * 0.5  # penalised
    else:
        time_score = min(1.0, (days_since_last_event - 30) / 60.0 + 0.5)

    # Absence: 0 recent events → 1.0, 3+ → 0.0
    absence_score = max(0.0, 1.0 - recent_event_count_30d / 3.0)

    novelty = (
        w_time * time_score
        + w_magnitude * magnitude_percentile
        + w_combination * combination_rarity
        + w_absence * absence_score
    )

    breakdown = {
        "time_score": round(time_score, 4),
        "magnitude_percentile": round(magnitude_percentile, 4),
        "combination_rarity": round(combination_rarity, 4),
        "absence_score": round(absence_score, 4),
        "novelty_score": round(novelty, 4),
    }
    return round(novelty, 4), breakdown


def compute_persistence_score(
    event_family: str,
    consecutive_confirmations: int,
    trend_strength: float,
    revision_stability: float,
    typical_duration_months: int,
) -> tuple[float, dict]:
    """
    Compute persistence score.

    Parameters
    ----------
    consecutive_confirmations : number of consecutive releases confirming same direction
    trend_strength            : linear trend R² over recent window (0–1)
    revision_stability        : 1 - (std of revisions / |mean|) clamped to [0, 1]
    typical_duration_months   : from macro_event_rules.yaml
    """
    w_duration = 0.40
    w_confirmations = 0.30
    w_trend = 0.20
    w_stability = 0.10

    # Duration: expected duration > 6 months → high persistence
    duration_score = min(1.0, typical_duration_months / 12.0)

    # Confirmations: 0 → 0.0, 3+ → 1.0
    confirmation_score = min(1.0, consecutive_confirmations / 3.0)

    persistence = (
        w_duration * duration_score
        + w_confirmations * confirmation_score
        + w_trend * trend_strength
        + w_stability * revision_stability
    )

    breakdown = {
        "duration_score": round(duration_score, 4),
        "confirmation_score": round(confirmation_score, 4),
        "trend_strength": round(trend_strength, 4),
        "revision_stability": round(revision_stability, 4),
        "persistence_score": round(persistence, 4),
    }
    return round(persistence, 4), breakdown


def compute_data_quality_score(
    source: str,
    frequency: str,
    has_vintage: bool,
    has_consensus: bool,
    has_previous_value: bool,
    availability_precision: str = "EXACT",
) -> float:
    """Start at 1.0, apply penalties from macro_event_rules.yaml and precision policy."""
    score = 1.0
    if not has_vintage:
        score -= 0.10
    if not has_consensus:
        score -= 0.05
    if not has_previous_value:
        score -= 0.10
    if frequency == "QUARTERLY":
        score -= 0.05
    if availability_precision == "ESTIMATED_MONTHLY" or availability_precision == "ESTIMATED_DAILY":
        score -= 0.10
    elif availability_precision == "UNKNOWN":
        score -= 0.55  # Heavy penalty for unknown publication timestamp

    return round(max(0.0, score), 4)
