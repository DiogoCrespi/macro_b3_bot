"""Transport explicit causal-path metadata to factor-specific company channels."""
from __future__ import annotations

from collections import defaultdict

from macro_b3_bot.domain.causal_models import SectorImpactCandidate
from macro_b3_bot.domain.company_exposure_models import CompanyFactorChannel


class CompanyChannelTransport:
    """No node-name inference: factor, sign, and channels come from graph metadata."""

    def from_sector_candidates(
        self, candidates: list[SectorImpactCandidate]
    ) -> list[CompanyFactorChannel]:
        grouped: dict[
            tuple[str, str, int, str],
            list[tuple[float, float, str, list[str], list[str]]],
        ] = defaultdict(list)
        for candidate in candidates:
            for path in candidate.causal_paths:
                for channel, channel_direction in path.company_channel_effects.items():
                    direction = path.factor_direction * channel_direction
                    grouped[
                        (path.factor, channel, direction, path.evidence_status)
                    ].append((
                        path.strength, path.confidence, path.path_id,
                        path.causal_edge_ids, path.evidence_ids,
                    ))

        output: list[CompanyFactorChannel] = []
        for (factor, channel, direction, evidence_status), observations in sorted(
            grouped.items()
        ):
            output.append(CompanyFactorChannel(
                factor=factor,
                channel=channel,
                direction=direction,
                strength=round(
                    sum(item[0] for item in observations) / len(observations), 4
                ),
                confidence=round(
                    sum(item[1] for item in observations) / len(observations), 4
                ),
                source_path_ids=sorted({item[2] for item in observations}),
                causal_edge_ids=sorted({
                    edge_id for item in observations for edge_id in item[3]
                }),
                evidence_ids=sorted({
                    evidence_id for item in observations for evidence_id in item[4]
                }),
                evidence_status=evidence_status,
            ))
        return output

    @staticmethod
    def aggregate(
        channels: list[CompanyFactorChannel],
    ) -> dict[tuple[str, str], float]:
        """Preserve factor identity through company-impact evaluation."""
        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for item in channels:
            grouped[(item.factor, item.channel)].append(
                item.direction * item.strength * item.confidence
            )
        return {
            key: max(-1.0, min(1.0, sum(values) / len(values)))
            for key, values in grouped.items()
        }
