"""Deterministic transport from causal paths to company financial channels."""
from __future__ import annotations

from collections import defaultdict

from macro_b3_bot.domain.causal_models import SectorImpactCandidate
from macro_b3_bot.domain.company_exposure_models import CompanyFactorChannel


# Direction is the effect of an upward/positive factor on the company channel.
_FACTOR_RULES: tuple[tuple[tuple[str, ...], str, tuple[tuple[str, int], ...]], ...] = (
    (("USD_BRL", "BRL_DEPRECI", "USD_STRENGTH"), "FX", (
        ("revenue", 1), ("cost", -1), ("debt", -1),
    )),
    (("SELIC", "INTEREST_RATE", "MONETARY"), "INTEREST_RATES", (
        ("debt", -1), ("demand", -1),
    )),
    (("INFLATION", "IPCA"), "INFLATION", (
        ("revenue", 1), ("cost", -1), ("debt", -1), ("demand", -1),
    )),
    (("BRENT", "OIL"), "OIL", (("revenue", 1), ("cost", -1))),
    (("GROWTH", "ACTIVITY", "GDP"), "ECONOMIC_ACTIVITY", (
        ("revenue", 1), ("demand", 1),
    )),
    (("ENSO", "EL_NINO", "LA_NINA"), "ENSO", (
        ("revenue", 1), ("cost", -1),
    )),
)


class CompanyChannelTransport:
    """Preserve factor and path context instead of collapsing to sector net impact."""

    def from_sector_candidates(
        self, candidates: list[SectorImpactCandidate]
    ) -> list[CompanyFactorChannel]:
        grouped: dict[tuple[str, str, int], list[tuple[float, float, list[str]]]] = (
            defaultdict(list)
        )
        for candidate in candidates:
            for path in candidate.causal_paths:
                text = "|".join(path).upper()
                match = next(
                    (rule for rule in _FACTOR_RULES if any(token in text for token in rule[0])),
                    None,
                )
                if match is None:
                    continue
                _, factor, channel_rules = match
                factor_direction = self._factor_direction(text)
                for channel, base_direction in channel_rules:
                    direction = factor_direction * base_direction
                    grouped[(factor, channel, direction)].append(
                        (abs(candidate.impact_score), candidate.confidence, path)
                    )

        output: list[CompanyFactorChannel] = []
        for (factor, channel, direction), observations in sorted(grouped.items()):
            strength = min(1.0, sum(item[0] for item in observations) / len(observations))
            confidence = sum(item[1] for item in observations) / len(observations)
            paths = [item[2] for item in observations]
            output.append(CompanyFactorChannel(
                factor=factor,
                channel=channel,
                direction=direction,
                strength=round(strength, 4),
                confidence=round(confidence, 4),
                evidence_ids=[
                    f"CAUSAL_PATH:{'>'.join(path)}" for path in paths
                ],
                source_paths=paths,
            ))
        return output

    @staticmethod
    def _factor_direction(path_text: str) -> int:
        negative_markers = (
            "_DOWN", "WEAKEN", "DOVISH", "FALLING", "BEARISH",
            "DISINFLATION", "DEPRECIATION_DOWN",
        )
        return -1 if any(marker in path_text for marker in negative_markers) else 1

    @staticmethod
    def aggregate(channels: list[CompanyFactorChannel]) -> dict[str, float]:
        by_channel: dict[str, list[float]] = defaultdict(list)
        for item in channels:
            by_channel[item.channel].append(
                item.direction * item.strength * item.confidence
            )
        return {
            channel: max(-1.0, min(1.0, sum(values) / len(values)))
            for channel, values in by_channel.items()
        }
