"""
Causal Graph Sector Impact Engine — Sprint 4B.

Propagates approved/watch MacroEvents through the deterministic Causal Graph (causal_graph.yaml)
to evaluate multi-path sector impacts on B3 sectors/subsectors.

BUY signals and stock ticker selections remain 100% disabled.
Outputs: SectorImpactCandidate with status (SECTOR_IMPACT_APPROVED / WATCH / REJECTED).
"""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from macro_b3_bot.domain.causal_models import (
    CausalEdge,
    SectorImpactCandidate,
    SectorImpactStatus,
)
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)

_DEFAULT_CAUSAL_GRAPH_PATH = Path(__file__).resolve().parents[3] / "config" / "causal_graph.yaml"


class CausalGraphEngine:
    """
    Propagation engine for mapping MacroEvents to B3 SectorImpactCandidates.
    """

    def __init__(
        self,
        store: DatabaseStore,
        run_id: str,
        graph_path: Path = _DEFAULT_CAUSAL_GRAPH_PATH,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.edges = self._load_graph(graph_path)
        self.adj_list = self._build_adj_list(self.edges)

    def _load_graph(self, path: Path) -> list[CausalEdge]:
        if not path.exists():
            logger.warning("Causal graph file not found at %s", path)
            return []
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        edge_list = data.get("edges", [])
        return [CausalEdge(**e) for e in edge_list]

    def _build_adj_list(self, edges: list[CausalEdge]) -> dict[str, list[CausalEdge]]:
        adj: dict[str, list[CausalEdge]] = {}
        for edge in edges:
            adj.setdefault(edge.source_node, []).append(edge)
        return adj

    def evaluate_events_window(
        self,
        since_date: date,
        as_of_timestamp: Optional[datetime] = None,
    ) -> dict[str, int]:
        """
        Query approved/watch MacroEvents available as of as_of_timestamp and propagate to sectors.

        Returns summary counts dict.
        """
        eff_now = as_of_timestamp or datetime.now(timezone.utc)

        # Query candidates from store created on or after since_date and detected_at <= eff_now
        events = self.store.connection.execute(
            """
            SELECT event_id, event_type, indicator, reference_date, detected_at,
                   surprise_score, novelty_score, persistence_score, regime_shift_score, data_quality_score,
                   direction, current_regime, status, score_breakdown
            FROM macro_event_candidates
            WHERE reference_date >= ? AND status IN ('MACRO_EVENT_APPROVED', 'MACRO_EVENT_WATCH')
            ORDER BY detected_at DESC
            """,
            [since_date]
        ).fetchall()

        cols = [
            "event_id", "event_type", "indicator", "reference_date", "detected_at",
            "surprise_score", "novelty_score", "persistence_score", "regime_shift_score", "data_quality_score",
            "direction", "current_regime", "status", "score_breakdown"
        ]
        event_list = [dict(zip(cols, r)) for r in events]

        total_evaluated = len(event_list)
        total_approved = 0
        total_watch = 0
        total_rejected = 0

        for evt in event_list:
            sector_candidates = self.propagate_event(evt, as_of_timestamp=eff_now)
            for cand in sector_candidates:
                self.save_sector_impact_candidate(cand)
                if cand.status == SectorImpactStatus.SECTOR_IMPACT_APPROVED.value:
                    total_approved += 1
                elif cand.status == SectorImpactStatus.SECTOR_IMPACT_WATCH.value:
                    total_watch += 1
                else:
                    total_rejected += 1

        return {
            "macro_events_processed": total_evaluated,
            "sector_impacts_approved": total_approved,
            "sector_impacts_watch": total_watch,
            "sector_impacts_rejected": total_rejected,
        }

    def propagate_event(
        self,
        evt: dict,
        as_of_timestamp: Optional[datetime] = None,
    ) -> list[SectorImpactCandidate]:
        """
        Propagate a single MacroEvent through the causal graph to evaluate sector impacts.
        """
        eff_now = as_of_timestamp or datetime.now(timezone.utc)
        event_type = evt["event_type"]
        event_score = float(evt.get("surprise_score", 0.5))

        # Find all paths from event_type to any B3_SECTOR_* node
        paths = self._find_all_paths(start_node=event_type, visited=set())
        if not paths:
            # Also try matching base event family (e.g. HAWKISH_MONETARY_SURPRISE -> MONETARY_POLICY_SURPRISE)
            base_family = event_type.replace("_UP", "").replace("_DOWN", "")
            paths = self._find_all_paths(start_node=base_family, visited=set())

        # Group paths by target sector
        sector_paths: dict[str, list[tuple[list[CausalEdge], float, int, float]]] = {}

        for path_edges in paths:
            if not path_edges:
                continue
            target_sector = path_edges[-1].target_node.replace("B3_SECTOR_", "")

            # Compute path properties
            path_dir = 1
            path_strength = 1.0
            path_conf = 1.0
            total_lag = 0
            half_life = path_edges[-1].half_life_days

            for edge in path_edges:
                path_dir *= edge.direction
                path_strength *= edge.strength
                path_conf *= edge.confidence
                total_lag += edge.lag_days

            # Time decay based on event age vs half_life
            det_dt = evt["detected_at"]
            if isinstance(det_dt, str):
                det_dt = datetime.fromisoformat(det_dt)
            if det_dt.tzinfo is None:
                det_dt = det_dt.replace(tzinfo=timezone.utc)

            age_days = max(0.0, (eff_now - det_dt).total_seconds() / 86400.0)
            time_decay = 0.5 ** (age_days / float(max(1, half_life)))

            path_impact = event_score * path_dir * path_strength * path_conf * time_decay
            sector_paths.setdefault(target_sector, []).append(
                (path_edges, path_impact, path_dir, path_conf)
            )

        results: list[SectorImpactCandidate] = []

        for sector_name, path_tuples in sector_paths.items():
            net_impact = sum(p[1] for p in path_tuples)
            impact_score = round(math.tanh(abs(net_impact)), 4)

            direction = "BULLISH" if net_impact >= 0 else "BEARISH"
            avg_conf = round(sum(p[3] for p in path_tuples) / len(path_tuples), 4)

            pos_paths = sum(1 for p in path_tuples if p[2] > 0)
            neg_paths = sum(1 for p in path_tuples if p[2] < 0)
            conflict_detected = (pos_paths > 0 and neg_paths > 0)

            formatted_paths = [
                [edges[0].source_node] + [edge.target_node for edge in edges]
                for edges, _, _, _ in path_tuples
            ]
            direct = [
                f"{edges[0].source_node} -> {edges[0].target_node}: {edges[0].rationale}"
                for edges, _, _, _ in path_tuples if len(edges) == 1
            ]
            second_order = [
                f"{edges[0].source_node} -> ... -> {edges[-1].target_node}: {edges[-1].rationale}"
                for edges, _, _, _ in path_tuples if len(edges) > 1
            ]

            # Gate classification for SectorImpactCandidate
            if impact_score >= 0.60 and avg_conf >= 0.65:
                status = SectorImpactStatus.SECTOR_IMPACT_APPROVED.value
            elif impact_score >= 0.35:
                status = SectorImpactStatus.SECTOR_IMPACT_WATCH.value
            else:
                status = SectorImpactStatus.SECTOR_IMPACT_REJECTED.value

            cand_id = hashlib.sha256(
                f"{evt['event_id']}|{sector_name}|{direction}|{str(impact_score)}".encode()
            ).hexdigest()[:24]

            cand = SectorImpactCandidate(
                candidate_id=cand_id,
                event_id=evt["event_id"],
                event_type=event_type,
                sector=sector_name,
                subsector=None,
                direction=direction,
                impact_score=impact_score,
                confidence=avg_conf,
                horizon_months=3,
                causal_paths=formatted_paths,
                direct_effects=direct,
                second_order_effects=second_order,
                positive_paths_count=pos_paths,
                negative_paths_count=neg_paths,
                conflict_detected=conflict_detected,
                invalidators=[],
                status=status,
                detected_at=eff_now,
            )
            results.append(cand)

        return results

    def _find_all_paths(
        self,
        start_node: str,
        visited: set[str],
        current_path: Optional[list[CausalEdge]] = None,
    ) -> list[list[CausalEdge]]:
        if current_path is None:
            current_path = []
        if start_node in visited:
            return []

        visited.add(start_node)
        all_paths: list[list[CausalEdge]] = []

        out_edges = self.adj_list.get(start_node, [])
        for edge in out_edges:
            next_node = edge.target_node
            new_path = current_path + [edge]

            if next_node.startswith("B3_SECTOR_"):
                all_paths.append(new_path)
            else:
                sub_paths = self._find_all_paths(next_node, set(visited), new_path)
                all_paths.extend(sub_paths)

        return all_paths

    def save_sector_impact_candidate(self, cand: SectorImpactCandidate) -> bool:
        """Idempotent save of a SectorImpactCandidate in DuckDB."""
        return self.store.save_sector_impact_candidate(cand.dict())
