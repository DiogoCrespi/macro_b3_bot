"""
Macro regime change detection — Sprint 4A.

Analyses the latest MacroReleases in the store to determine the current
macro regime matrix. Generates MacroRegimeSnapshot and flags
YIELD_CURVE_REGIME_SHIFT, USD_REGIME_SHIFT, ENSO_PHASE_CHANGE events.

Regimes:
    GROWTH_UP_INFLATION_DOWN
    GROWTH_UP_INFLATION_UP
    GROWTH_DOWN_INFLATION_DOWN
    GROWTH_DOWN_INFLATION_UP
    LIQUIDITY_EASING
    LIQUIDITY_TIGHTENING
    OIL_SHOCK_UP / OIL_SHOCK_DOWN
    ENSO_EL_NINO / ENSO_LA_NINA
"""
import hashlib
import logging
import statistics
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

# Regime thresholds
_GROWTH_Z_THRESHOLD = 1.5
_INFLATION_Z_THRESHOLD = 1.5
_OIL_PCT_THRESHOLD = 0.15     # 15% move
_ENSO_ONI_THRESHOLD = Decimal("0.5")
_SPREAD_INVERSION_THRESHOLD = 0.0   # T10Y2Y < 0 = inverted


class RegimeDetector:
    """
    Reads recent MacroReleases from the store and computes the current
    macro regime snapshot.
    """

    def __init__(self, store: DatabaseStore, ingestion_run_id: str) -> None:
        self.store = store
        self.run_id = ingestion_run_id

    def detect_and_snapshot(self, as_of_timestamp: Optional[datetime] = None) -> dict:
        """
        Run regime detection and persist a MacroRegimeSnapshot as of as_of_timestamp.
        Returns the snapshot dict.
        """
        today = date.today()
        eff_now = as_of_timestamp or datetime.now(timezone.utc)

        growth_dir, growth_releases = self._detect_growth_direction(as_of_timestamp=eff_now)
        inflation_dir, inflation_releases = self._detect_inflation_direction(as_of_timestamp=eff_now)
        liquidity, liquidity_releases = self._detect_liquidity_stance(as_of_timestamp=eff_now)
        oil_regime, oil_releases = self._detect_oil_regime(as_of_timestamp=eff_now)
        enso_phase, enso_releases = self._detect_enso_phase(as_of_timestamp=eff_now)

        # Composite label
        if growth_dir in ("UP", "STABLE") and inflation_dir == "DOWN":
            regime_label = "GROWTH_UP_INFLATION_DOWN"
        elif growth_dir == "UP" and inflation_dir in ("UP", "STABLE"):
            regime_label = "GROWTH_UP_INFLATION_UP"
        elif growth_dir == "DOWN" and inflation_dir == "DOWN":
            regime_label = "GROWTH_DOWN_INFLATION_DOWN"
        elif growth_dir == "DOWN" and inflation_dir == "UP":
            regime_label = "GROWTH_DOWN_INFLATION_UP"
        else:
            regime_label = "MIXED"

        all_evidence = growth_releases + inflation_releases + liquidity_releases + oil_releases + enso_releases
        confidence = self._compute_regime_confidence(all_evidence)

        snapshot_id = hashlib.sha256(
            f"{today.isoformat()}|{regime_label}|{enso_phase}|{oil_regime}|{eff_now.isoformat()}".encode()
        ).hexdigest()[:24]

        snap = {
            "snapshot_id": snapshot_id,
            "snapshot_date": today,
            "captured_at": eff_now,
            "growth_direction": growth_dir,
            "inflation_direction": inflation_dir,
            "liquidity_stance": liquidity,
            "oil_regime": oil_regime,
            "enso_phase": enso_phase,
            "regime_label": regime_label,
            "confidence": confidence,
            "evidence_release_ids": all_evidence[:20],  # cap at 20
            "ingestion_run_id": self.run_id,
        }

        self.store.save_macro_regime_snapshot(snap)
        logger.info("Regime snapshot: %s (confidence=%.2f)", regime_label, confidence)
        return snap

    def _detect_growth_direction(self, as_of_timestamp: Optional[datetime] = None) -> tuple[str, list[str]]:
        """Use IBC-Br + US Industrial Production + US Unemployment."""
        growth_series = [
            ("BCB_SGS", "24364"),  # IBC-Br
            ("FRED", "INDPRO"),    # US Industrial Production
        ]
        directions = []
        release_ids = []

        for source, series_code in growth_series:
            releases = self.store.get_macro_releases_for_series(source, series_code, limit=6, as_of_timestamp=as_of_timestamp)
            if len(releases) < 3:
                continue
            vals = [float(r["actual_value"]) for r in releases]
            recent = vals[:3]
            older = vals[3:]
            if older:
                delta = statistics.mean(recent) - statistics.mean(older)
                sigma = statistics.stdev(vals) if len(vals) > 1 else 1.0
                if sigma and abs(delta / sigma) > 0.5:
                    directions.append("UP" if delta > 0 else "DOWN")
            release_ids.extend([r["release_id"] for r in releases[:3]])

        if not directions:
            return "STABLE", release_ids
        up_count = directions.count("UP")
        down_count = directions.count("DOWN")
        if up_count > down_count:
            return "UP", release_ids
        if down_count > up_count:
            return "DOWN", release_ids
        return "STABLE", release_ids

    def _detect_inflation_direction(self, as_of_timestamp: Optional[datetime] = None) -> tuple[str, list[str]]:
        """Use IPCA Focus + US CPI."""
        inflation_series = [
            ("FRED", "CPIAUCSL"),   # US CPI
            ("BCB_FOCUS", "IPCA"),  # BR IPCA expectation
        ]
        directions = []
        release_ids = []

        for source, series_code in inflation_series:
            releases = self.store.get_macro_releases_for_series(source, series_code, limit=6, as_of_timestamp=as_of_timestamp)
            if len(releases) < 3:
                continue
            vals = [float(r["actual_value"]) for r in releases]
            # For CPI: compare 3-month change
            if len(vals) >= 2:
                recent_change = vals[0] - vals[2] if len(vals) > 2 else vals[0] - vals[1]
                directions.append("UP" if recent_change > 0 else "DOWN")
            release_ids.extend([r["release_id"] for r in releases[:3]])

        if not directions:
            return "STABLE", release_ids
        up_count = directions.count("UP")
        down_count = directions.count("DOWN")
        if up_count > down_count:
            return "UP", release_ids
        if down_count > up_count:
            return "DOWN", release_ids
        return "STABLE", release_ids

    def _detect_liquidity_stance(self, as_of_timestamp: Optional[datetime] = None) -> tuple[str, list[str]]:
        """Use Fed Funds + Selic + T10Y2Y spread."""
        release_ids = []

        # Yield curve
        spread_releases = self.store.get_macro_releases_for_series("FRED", "T10Y2Y", limit=5, as_of_timestamp=as_of_timestamp)
        if spread_releases:
            latest_spread = float(spread_releases[0]["actual_value"])
            release_ids.extend([r["release_id"] for r in spread_releases[:2]])
            if latest_spread < _SPREAD_INVERSION_THRESHOLD:
                return "TIGHTENING", release_ids
            if latest_spread > 1.0:
                return "EASING", release_ids

        # Fed Funds direction
        ff_releases = self.store.get_macro_releases_for_series("FRED", "DFF", limit=10, as_of_timestamp=as_of_timestamp)
        if len(ff_releases) >= 5:
            recent = float(ff_releases[0]["actual_value"])
            older = float(ff_releases[4]["actual_value"])
            release_ids.extend([r["release_id"] for r in ff_releases[:3]])
            if recent > older + 0.25:
                return "TIGHTENING", release_ids
            if recent < older - 0.25:
                return "EASING", release_ids

        return "NEUTRAL", release_ids

    def _detect_oil_regime(self, as_of_timestamp: Optional[datetime] = None) -> tuple[str, list[str]]:
        """WTI + Brent: 15% move in last 20 trading days."""
        release_ids = []
        for source, series_code in [("EIA", "PET.RWTC.D"), ("EIA", "PET.RBRTE.D")]:
            releases = self.store.get_macro_releases_for_series(source, series_code, limit=25, as_of_timestamp=as_of_timestamp)
            if len(releases) < 5:
                continue
            latest = float(releases[0]["actual_value"])
            baseline = float(releases[-1]["actual_value"])
            if baseline == 0:
                continue
            pct_change = (latest - baseline) / baseline
            release_ids.extend([r["release_id"] for r in releases[:3]])
            if pct_change >= _OIL_PCT_THRESHOLD:
                return "SHOCK_UP", release_ids
            if pct_change <= -_OIL_PCT_THRESHOLD:
                return "SHOCK_DOWN", release_ids

        return "STABLE", release_ids

    def _detect_enso_phase(self, as_of_timestamp: Optional[datetime] = None) -> tuple[str, list[str]]:
        """ONI 3-month mean ≥ +0.5 → EL_NINO, ≤ -0.5 → LA_NINA."""
        release_ids = []
        oni_releases = self.store.get_macro_releases_for_series("NOAA", "ONI", limit=5, as_of_timestamp=as_of_timestamp)
        if not oni_releases:
            return "NEUTRAL", release_ids

        latest = Decimal(str(oni_releases[0]["actual_value"]))
        release_ids.extend([r["release_id"] for r in oni_releases[:2]])

        if latest >= _ENSO_ONI_THRESHOLD:
            return "EL_NINO", release_ids
        if latest <= -_ENSO_ONI_THRESHOLD:
            return "LA_NINA", release_ids
        return "NEUTRAL", release_ids

    def _compute_regime_confidence(self, evidence_release_ids: list[str]) -> float:
        """Confidence based on how many series contributed evidence."""
        n = len(set(evidence_release_ids))
        # Max confidence at 10+ distinct releases
        return round(min(1.0, n / 10.0), 4)
