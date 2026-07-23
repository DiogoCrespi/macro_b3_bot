"""
MacroEventCandidate builder and MacroEventGate — Sprint 4A.

For each new MacroRelease:
1. Compute all scores (surprise, novelty, persistence, regime_shift, data_quality)
2. Determine event_type and direction
3. Build MacroEventCandidate
4. Apply MacroEventGate thresholds → status (APPROVED / WATCH / REJECTED)
5. Persist to DuckDB

BUY is never generated here. Output is limited to MacroEventCandidate with status.
"""
from __future__ import annotations

import hashlib
import json
import logging
import statistics
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml

from macro_b3_bot.application.detect_macro_surprises import (
    compute_data_quality_score,
    compute_novelty_score,
    compute_persistence_score,
    compute_surprise_score,
)
from macro_b3_bot.domain.macro_event_models import MACRO_EVENT_TYPES
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "macro_event_rules.yaml"
_SERIES_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "global_macro_series.yaml"

# Lookahead safety: never process releases where available_at > NOW
_SAFETY_CHECK_LOOKAHEAD = True


def _load_rules(rules_path: Path = _RULES_PATH) -> dict:
    with open(rules_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_series_map(config_path: Path = _SERIES_CONFIG_PATH) -> dict[str, dict]:
    """Return {(source, series_code): cfg} mapping."""
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {(s["source"], s["series_code"]): s for s in data["series"]}


def _event_type_from_family(event_family: str, surprise_score: float, direction: str) -> str:
    """Map event_family + context → MacroEventType."""
    mapping = {
        "MONETARY_POLICY_SURPRISE": "MONETARY_POLICY_SURPRISE",
        "INFLATION_SURPRISE": "INFLATION_SURPRISE",
        "GROWTH_SURPRISE": "GROWTH_SURPRISE",
        "YIELD_CURVE_REGIME_SHIFT": "YIELD_CURVE_REGIME_SHIFT",
        "USD_REGIME_SHIFT": "USD_REGIME_SHIFT",
        "OIL_PRICE_SHOCK": "OIL_PRICE_SHOCK",
        "OIL_INVENTORY_SHOCK": "OIL_INVENTORY_SHOCK",
        "ENERGY_SUPPLY_SHOCK": "ENERGY_SUPPLY_SHOCK",
        "ENSO_PHASE_CHANGE": "ENSO_PHASE_CHANGE",
        "ENSO_INTENSIFICATION": "ENSO_INTENSIFICATION",
        "BRAZIL_EXPECTATION_REPRICING": "BRAZIL_EXPECTATION_REPRICING",
    }
    return mapping.get(event_family, event_family)


def _determine_direction(
    actual: Decimal,
    previous: Optional[Decimal],
    consensus: Optional[Decimal],
    series_code: str,
) -> str:
    """
    Compute event direction based on surprise vs expectation and series context.
    """
    # Oil-specific
    if "PET." in series_code or "WTI" in series_code or "BRENT" in series_code:
        if previous and actual > previous:
            return "BULLISH_OIL"
        return "BEARISH_OIL"

    # Rate/monetary policy
    if series_code in ("DFF", "11", "Selic"):
        if previous and actual > previous:
            return "HAWKISH"
        return "DOVISH"

    # USD
    if series_code in ("DTWEXBGS", "1", "Cambio"):
        if previous and actual > previous:
            return "USD_STRENGTHENING"
        return "USD_WEAKENING"

    # ENSO
    if series_code in ("NINO34", "ONI"):
        if actual >= Decimal("0.5"):
            return "EL_NINO"
        if actual <= Decimal("-0.5"):
            return "LA_NINA"
        return "NEUTRAL_ENSO"

    # Generic
    if consensus is not None:
        deviation = actual - consensus
        if deviation > 0:
            return "ABOVE_EXPECTATIONS"
        return "BELOW_EXPECTATIONS"

    if previous is not None:
        if actual > previous:
            return "RISING"
        if actual < previous:
            return "FALLING"

    return "NEUTRAL"


class MacroEventBuilder:
    """
    Processes new MacroReleases and generates MacroEventCandidates.

    Usage:
        builder = MacroEventBuilder(store, run_id)
        results = builder.process_since(since_date)
    """

    def __init__(self, store: DatabaseStore, ingestion_run_id: str) -> None:
        self.store = store
        self.run_id = ingestion_run_id
        self.rules = _load_rules()
        self.series_map = _load_series_map()
        self.now = datetime.now(timezone.utc)

    def process_window(
        self,
        history_start: date,
        history_end: date,
        as_of_timestamp: Optional[datetime] = None,
    ) -> dict:
        """
        Process MacroReleases within a strict Point-In-Time window [history_start, history_end]
        as of as_of_timestamp.

        Returns detailed summary:
        - records_scanned
        - records_eligible
        - future_excluded
        - releases_evaluated
        - events_approved / watch / rejected
        """
        effective_now = as_of_timestamp or self.now

        # 1. Total records scanned in date window
        scanned_row = self.store.connection.execute(
            "SELECT COUNT(*) FROM macro_releases WHERE reference_date >= ? AND reference_date <= ?",
            [history_start, history_end]
        ).fetchone()
        records_scanned = scanned_row[0] if scanned_row else 0

        # 2. Query eligible records available as of effective_now
        releases = self.store.connection.execute(
            """
            SELECT release_id, source, series_code, indicator, geography, frequency, unit,
                   reference_date, published_at, available_at,
                   actual_value, previous_value, consensus_value,
                   raw_checksum, record_checksum, availability_precision
            FROM macro_releases
            WHERE reference_date >= ? AND reference_date <= ? AND available_at <= ?
            ORDER BY source, series_code, reference_date
            """,
            [history_start, history_end, effective_now]
        ).fetchall()

        cols = ["release_id", "source", "series_code", "indicator", "geography", "frequency", "unit",
                "reference_date", "published_at", "available_at",
                "actual_value", "previous_value", "consensus_value",
                "raw_checksum", "record_checksum", "availability_precision"]
        release_list = [dict(zip(cols, r)) for r in releases]

        records_eligible = len(release_list)
        future_excluded = max(0, records_scanned - records_eligible)

        approved = 0
        watch = 0
        rejected = 0
        skipped = 0

        for rel in release_list:
            series_key = (rel["source"], rel["series_code"])
            series_cfg = self.series_map.get(series_key)
            if not series_cfg:
                skipped += 1
                continue

            event_family = series_cfg.get("event_family", "")
            if event_family not in MACRO_EVENT_TYPES:
                skipped += 1
                continue

            status = self._build_and_gate(rel, series_cfg, event_family, effective_now=effective_now)
            if status == "MACRO_EVENT_APPROVED":
                approved += 1
            elif status == "MACRO_EVENT_WATCH":
                watch += 1
            else:
                rejected += 1

        return {
            "records_scanned": records_scanned,
            "records_eligible": records_eligible,
            "future_excluded": future_excluded,
            "releases_evaluated": len(release_list) - skipped,
            "events_approved": approved,
            "events_watch": watch,
            "events_rejected": rejected,
            "skipped": skipped,
        }

    def process_since(self, since_date: date, as_of_timestamp: Optional[datetime] = None) -> dict:
        """
        Process all MacroReleases published since since_date and generate
        MacroEventCandidates for those that pass the gate as of as_of_timestamp.

        Returns summary dict.
        """
        effective_now = as_of_timestamp or self.now
        # Fetch new releases from the store
        releases = self.store.connection.execute(
            """
            SELECT release_id, source, series_code, indicator, geography, frequency, unit,
                   reference_date, published_at, available_at,
                   actual_value, previous_value, consensus_value,
                   raw_checksum, record_checksum, availability_precision
            FROM macro_releases
            WHERE reference_date >= ?
            ORDER BY source, series_code, reference_date
            """,
            [since_date]
        ).fetchall()

        cols = ["release_id", "source", "series_code", "indicator", "geography", "frequency", "unit",
                "reference_date", "published_at", "available_at",
                "actual_value", "previous_value", "consensus_value",
                "raw_checksum", "record_checksum", "availability_precision"]
        release_list = [dict(zip(cols, r)) for r in releases]

        approved = 0
        watch = 0
        rejected = 0
        skipped = 0

        for rel in release_list:
            if _SAFETY_CHECK_LOOKAHEAD:
                avail = rel["available_at"]
                if avail is not None:
                    if isinstance(avail, datetime) and avail.tzinfo is None:
                        avail = avail.replace(tzinfo=timezone.utc)
                    if isinstance(avail, datetime) and avail > effective_now:
                        logger.warning("LOOK-AHEAD DETECTED: %s available_at=%s, skipping", rel["release_id"], avail)
                        skipped += 1
                        continue

            series_key = (rel["source"], rel["series_code"])
            series_cfg = self.series_map.get(series_key)
            if not series_cfg:
                skipped += 1
                continue

            event_family = series_cfg.get("event_family", "")
            if event_family not in MACRO_EVENT_TYPES:
                skipped += 1
                continue

            status = self._build_and_gate(rel, series_cfg, event_family, effective_now=effective_now)
            if status == "MACRO_EVENT_APPROVED":
                approved += 1
            elif status == "MACRO_EVENT_WATCH":
                watch += 1
            else:
                rejected += 1

        return {
            "releases_evaluated": len(release_list),
            "events_approved": approved,
            "events_watch": watch,
            "events_rejected": rejected,
            "skipped": skipped,
        }

    def _build_and_gate(self, rel: dict, series_cfg: dict, event_family: str, effective_now: Optional[datetime] = None) -> str:
        """Build a MacroEventCandidate and apply the gate. Returns final status."""
        source = rel["source"]
        series_code = rel["series_code"]
        ref_date = rel["reference_date"]
        eff_now = effective_now or self.now
        if isinstance(ref_date, str):
            ref_date = date.fromisoformat(ref_date)

        actual = Decimal(str(rel["actual_value"]))
        previous = Decimal(str(rel["previous_value"])) if rel["previous_value"] is not None else None
        consensus = Decimal(str(rel["consensus_value"])) if rel["consensus_value"] is not None else None

        # Historical context (reverse so order is chronological: oldest -> newest)
        historical_releases = self.store.get_macro_releases_for_series(source, series_code, limit=60, as_of_timestamp=eff_now)
        hist_values = [Decimal(str(r["actual_value"])) for r in reversed(historical_releases) if r["actual_value"] is not None]
        if hist_values and hist_values[-1] == actual:
            hist_values = hist_values[:-1]
        hist_values = hist_values[-50:]

        # ── Scores ─────────────────────────────────────────────────────────
        surprise_score, surprise_breakdown = compute_surprise_score(
            actual=actual,
            consensus=consensus,
            historical_values=hist_values,
        )

        # Novelty
        days_since = self._days_since_last_event(source, series_code, event_family, ref_date, as_of_timestamp=eff_now)
        magnitude_pct = self._magnitude_percentile(actual, hist_values)
        recent_30d = self._recent_event_count(source, series_code, event_family, 30, as_of_timestamp=eff_now)
        novelty_score, novelty_breakdown = compute_novelty_score(
            event_type=event_family,
            series_code=series_code,
            current_score=surprise_score,
            days_since_last_event=days_since,
            magnitude_percentile=magnitude_pct,
            combination_rarity=0.5,   # simplified in Sprint 4A
            recent_event_count_30d=recent_30d,
        )

        # Persistence
        typical_duration = self.rules.get("persistence", {}).get("typical_durations", {}).get(event_family, 3)
        consecutive = self._consecutive_confirmations(source, series_code, actual, hist_values)
        persistence_score, persistence_breakdown = compute_persistence_score(
            event_family=event_family,
            consecutive_confirmations=consecutive,
            trend_strength=self._compute_trend_strength(hist_values),
            revision_stability=0.85,  # simplified in Sprint 4A
            typical_duration_months=typical_duration,
        )

        # Regime shift (simplified: same as persistence for now)
        regime_shift_score = round(0.5 * surprise_score + 0.5 * persistence_score, 4)

        # Data quality — check actual vintage count for this release
        vint_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM macro_data_vintages WHERE source = ? AND series_code = ? AND reference_date = ?",
            [source, series_code, ref_date]
        ).fetchone()[0]
        has_vintage = vint_count > 0
        precision = rel.get("availability_precision", "EXACT")

        data_quality_score = compute_data_quality_score(
            source=source,
            frequency=series_cfg.get("frequency", "MONTHLY"),
            has_vintage=has_vintage,
            availability_precision=precision,
        )

        # Direction
        direction = _determine_direction(actual, previous, consensus, series_code)

        # ── Gate ───────────────────────────────────────────────────────────
        gate_rules = self.rules.get("gate", {})
        status, failed_conditions = self._apply_gate(
            surprise_score, novelty_score, persistence_score,
            regime_shift_score, data_quality_score, gate_rules,
            availability_precision=precision,
        )

        # ── Build candidate ─────────────────────────────────────────────────
        event_type = _event_type_from_family(event_family, surprise_score, direction)
        event_id = hashlib.sha256(
            f"{event_type}|{source}|{series_code}|{ref_date.isoformat()}|{str(actual)}".encode()
        ).hexdigest()[:24]

        score_breakdown = {
            "surprise": surprise_breakdown,
            "novelty": novelty_breakdown,
            "persistence": persistence_breakdown,
            "failed_conditions": failed_conditions,
        }

        horizon_months = self.rules.get("horizons", {}).get(event_family, 3)
        affected_variables = self._get_affected_variables(event_family, series_code)
        current_regime = self._get_current_regime(as_of_timestamp=eff_now)

        evt = {
            "event_id": event_id,
            "event_type": event_type,
            "indicator": rel["indicator"],
            "geography": json.loads(rel["geography"]) if isinstance(rel["geography"], str) else rel["geography"],
            "affected_variables": affected_variables,
            "reference_date": ref_date,
            "detected_at": eff_now,
            "horizon_months": horizon_months,
            "actual_value": actual,
            "expected_value": consensus,
            "surprise_value": (actual - consensus) if consensus else None,
            "surprise_score": surprise_score,
            "novelty_score": novelty_score,
            "persistence_score": persistence_score,
            "regime_shift_score": regime_shift_score,
            "data_quality_score": data_quality_score,
            "direction": direction,
            "current_regime": current_regime,
            "evidence_ids": [rel["release_id"]],
            "status": status,
            "score_breakdown": score_breakdown,
        }

        self.store.save_macro_event_candidate(evt)
        logger.info(
            "[%s] %s %s → %s (surprise=%.2f novelty=%.2f regime=%.2f quality=%.2f)",
            source, series_code, ref_date, status,
            surprise_score, novelty_score, regime_shift_score, data_quality_score
        )
        return status

    def _apply_gate(
        self,
        surprise: float,
        novelty: float,
        persistence: float,
        regime_shift: float,
        quality: float,
        gate_rules: dict,
        availability_precision: str = "EXACT",
    ) -> tuple[str, list[str]]:
        min_surprise = gate_rules.get("min_surprise_score", 0.60)
        min_regime = gate_rules.get("min_regime_shift_score", 0.65)
        min_novelty = gate_rules.get("min_novelty_score", 0.50)
        min_quality = gate_rules.get("min_data_quality_score", 0.80)
        min_watch_surprise = gate_rules.get("watch_min_surprise_score", 0.40)

        passes_primary = (surprise >= min_surprise) or (regime_shift >= min_regime)
        passes_novelty = novelty >= min_novelty
        passes_quality = quality >= min_quality

        failed_conditions = []
        if not passes_primary:
            failed_conditions.append("SURPRISE_AND_REGIME_BELOW_THRESHOLD")
        if not passes_novelty:
            failed_conditions.append("NOVELTY_BELOW_THRESHOLD")
        if not passes_quality:
            failed_conditions.append("QUALITY_BELOW_THRESHOLD")

        status = "MACRO_EVENT_REJECTED"
        if passes_primary and passes_novelty and passes_quality:
            status = "MACRO_EVENT_APPROVED"
        elif passes_novelty and passes_quality and surprise >= min_watch_surprise:
            status = "MACRO_EVENT_WATCH"

        # Explicit lockout: UNKNOWN availability precision can NEVER be MACRO_EVENT_APPROVED
        if availability_precision == "UNKNOWN":
            failed_conditions.append("AVAILABILITY_PRECISION_UNKNOWN")
            if status == "MACRO_EVENT_APPROVED":
                status = "MACRO_EVENT_WATCH"

        return status, failed_conditions

    def _days_since_last_event(
        self, source: str, series_code: str, event_family: str, ref_date: date, as_of_timestamp: Optional[datetime] = None
    ) -> Optional[int]:
        eff_now = as_of_timestamp or self.now
        row = self.store.connection.execute(
            """
            SELECT MAX(reference_date) FROM macro_event_candidates
            WHERE event_type = ? AND reference_date < ? AND detected_at <= ?
            """,
            [event_family, ref_date, eff_now]
        ).fetchone()
        if row and row[0]:
            last = row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
            return (ref_date - last).days
        return None

    def _recent_event_count(
        self, source: str, series_code: str, event_family: str, days: int, as_of_timestamp: Optional[datetime] = None
    ) -> int:
        from datetime import timedelta
        eff_now = as_of_timestamp or self.now
        as_of_dt = eff_now.date() if isinstance(eff_now, datetime) else eff_now
        cutoff = as_of_dt - timedelta(days=days)
        row = self.store.connection.execute(
            """
            SELECT COUNT(*) FROM macro_event_candidates
            WHERE event_type = ? AND reference_date >= ? AND reference_date <= ? AND detected_at <= ?
            """,
            [event_family, cutoff, as_of_dt, eff_now]
        ).fetchone()
        return row[0] if row else 0

    def _magnitude_percentile(self, actual: Decimal, historical: list[Decimal]) -> float:
        if not historical:
            return 0.5
        n = len(historical)
        rank = sum(1 for v in historical if abs(v) <= abs(actual))
        return round(rank / n, 4)

    def _consecutive_confirmations(self, source: str, series_code: str, actual: Decimal, historical: list[Decimal]) -> int:
        if len(historical) < 2:
            return 0
        direction = 1 if actual >= historical[0] else -1
        count = 0
        for i in range(len(historical) - 1):
            if (historical[i] - historical[i + 1]) * direction > 0:
                count += 1
            else:
                break
        return count

    def _compute_trend_strength(self, values: list[Decimal]) -> float:
        if len(values) < 4:
            return 0.0
        n = min(12, len(values))
        recent = [float(v) for v in values[:n]]
        x_bar = statistics.mean(range(n))
        y_bar = statistics.mean(recent)
        ss_xy = sum((i - x_bar) * (y - y_bar) for i, y in enumerate(recent))
        ss_xx = sum((i - x_bar) ** 2 for i in range(n))
        ss_yy = sum((y - y_bar) ** 2 for y in recent)
        if ss_xx == 0 or ss_yy == 0:
            return 0.0
        r = ss_xy / (ss_xx ** 0.5 * ss_yy ** 0.5)
        return round(r ** 2, 4)  # R²

    def _get_affected_variables(self, event_family: str, series_code: str) -> list[str]:
        mapping = {
            "MONETARY_POLICY_SURPRISE": ["interest_rates", "credit_spreads", "brl_usd"],
            "INFLATION_SURPRISE": ["real_yields", "consumption", "wages"],
            "GROWTH_SURPRISE": ["earnings", "revenue", "capex"],
            "YIELD_CURVE_REGIME_SHIFT": ["bank_margins", "duration_risk", "credit_risk"],
            "USD_REGIME_SHIFT": ["commodities", "exports", "em_debt"],
            "OIL_PRICE_SHOCK": ["energy_costs", "inflation", "petrobras"],
            "OIL_INVENTORY_SHOCK": ["wti_spread", "refinery_margins"],
            "ENERGY_SUPPLY_SHOCK": ["energy_prices", "grid_costs"],
            "ENSO_PHASE_CHANGE": ["agro_yield", "hydrology", "electricity_generation"],
            "ENSO_INTENSIFICATION": ["food_prices", "water_supply"],
            "BRAZIL_EXPECTATION_REPRICING": ["selic", "brl_usd", "long_yields"],
        }
        return mapping.get(event_family, [])

    def _get_current_regime(self, as_of_timestamp: Optional[datetime] = None) -> str:
        """Return the most recent regime label as of as_of_timestamp from snapshot table."""
        eff_now = as_of_timestamp or self.now
        as_of_dt = eff_now.date() if isinstance(eff_now, datetime) else eff_now
        row = self.store.connection.execute(
            "SELECT regime_label FROM macro_regime_snapshots WHERE snapshot_date <= ? ORDER BY snapshot_date DESC, captured_at DESC LIMIT 1",
            [as_of_dt]
        ).fetchone()
        return row[0] if row else "MIXED"
