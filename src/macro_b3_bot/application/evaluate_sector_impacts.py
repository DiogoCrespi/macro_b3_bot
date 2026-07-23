"""Point-in-time macro-event to B3-sector causal propagation (Sprint 4B.1)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from macro_b3_bot.domain.causal_models import (
    CausalEdge,
    CausalPath,
    SectorImpactCandidate,
    SectorImpactStatus,
    SectorStateSnapshot,
)
from macro_b3_bot.domain.macro_event_models import MACRO_EVENT_TYPES
from macro_b3_bot.infrastructure.store import DatabaseStore

logger = logging.getLogger(__name__)
_DEFAULT_CAUSAL_GRAPH_PATH = Path(__file__).resolve().parents[3] / "config" / "causal_graph.yaml"


_DIRECTION_SUFFIXES = {
    "HAWKISH": "HAWKISH", "DOVISH": "DOVISH",
    "RISING": "UP", "FALLING": "DOWN",
    "ABOVE_EXPECTATIONS": "UP", "BELOW_EXPECTATIONS": "DOWN",
    "BULLISH_OIL": "UP", "BEARISH_OIL": "DOWN",
    "USD_STRENGTHENING": "UP", "USD_WEAKENING": "DOWN",
    "EL_NINO": "EL_NINO", "LA_NINA": "LA_NINA", "NEUTRAL_ENSO": "NEUTRAL",
    "NEUTRAL": "NEUTRAL",
}


def causal_root(event_type: str, direction: str) -> str:
    """Build the graph namespace from the builder's real event contract."""
    suffix = _DIRECTION_SUFFIXES.get(direction)
    if suffix is None:
        raise ValueError(f"Unsupported macro-event direction: {event_type}/{direction}")
    aliases = {
        ("MONETARY_POLICY_SURPRISE", "HAWKISH"): "MONETARY_POLICY_HAWKISH",
        ("MONETARY_POLICY_SURPRISE", "DOVISH"): "MONETARY_POLICY_DOVISH",
    }
    return aliases.get((event_type, direction), f"{event_type}_{suffix}")


class CausalGraphEngine:
    def __init__(self, store: DatabaseStore, run_id: str, graph_path: Path = _DEFAULT_CAUSAL_GRAPH_PATH) -> None:
        self.store = store
        self.run_id = run_id
        self.graph_version, self.edges = self._load_graph(graph_path)
        self.adj_list = self._build_adj_list(self.edges)
        self._validate_company_metadata()

    def _load_graph(self, path: Path) -> tuple[str, list[CausalEdge]]:
        if not path.exists():
            logger.warning("Causal graph file not found at %s", path)
            return "missing", []
        with open(path, encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        edges = [CausalEdge(**edge) for edge in data.get("edges", [])]
        return str(data.get("graph_version", "unversioned")), edges

    @staticmethod
    def _build_adj_list(edges: list[CausalEdge]) -> dict[str, list[CausalEdge]]:
        adjacency: dict[str, list[CausalEdge]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source_node, []).append(edge)
        return adjacency

    def validate_event_coverage(self) -> dict[str, list[str]]:
        covered = {edge.source_node.rsplit("_", 1)[0] for edge in self.edges}
        missing = []
        for event_type in sorted(MACRO_EVENT_TYPES):
            if not any(root.startswith(event_type) or root in {"MONETARY_POLICY_HAWKISH", "MONETARY_POLICY_DOVISH"}
                       and event_type == "MONETARY_POLICY_SURPRISE" for root in self.adj_list):
                missing.append(event_type)
        return {"missing": missing, "covered": sorted(covered)}

    def _validate_company_metadata(self) -> None:
        targets = {edge.target_node for edge in self.edges}
        roots = {edge.source_node for edge in self.edges if edge.source_node not in targets}
        for root in roots:
            for path in self._find_all_paths(root, set()):
                metadata = [edge for edge in path if edge.factor]
                if len(metadata) != 1 or not metadata[0].company_channel_effects:
                    edge_ids = ">".join(edge.edge_id for edge in path)
                    raise ValueError(
                        f"causal path {edge_ids} must have exactly one factor edge "
                        "with company_channel_effects"
                    )

    def evaluate_events_window(
        self, since_date: date, as_of_timestamp: Optional[datetime] = None,
        event_run_id: Optional[str] = None,
    ) -> dict[str, int | str]:
        eff_now = self._utc(as_of_timestamp or datetime.now(timezone.utc))
        source_run_id = event_run_id or self.run_id or self.store.get_latest_macro_event_run_id()
        if not source_run_id:
            return self._summary(0, [])
        rows = self.store.connection.execute(
            """
            SELECT e.event_id, e.event_type, e.indicator, e.reference_date, e.detected_at,
                   e.surprise_score, e.novelty_score, e.persistence_score,
                   e.regime_shift_score, e.data_quality_score, e.direction,
                   e.current_regime, e.status, e.score_breakdown, e.ingestion_run_id,
                   COALESCE((SELECT MAX(r.available_at)
                     FROM macro_event_evidence_links l JOIN macro_releases r ON r.release_id=l.release_id
                     WHERE l.event_id=e.event_id), e.detected_at) AS event_available_at
            FROM macro_event_candidates e
            WHERE e.ingestion_run_id = ?
              AND e.status IN ('MACRO_EVENT_APPROVED', 'MACRO_EVENT_WATCH')
              AND e.reference_date BETWEEN ? AND CAST(? AS DATE)
              AND e.detected_at <= ?
            ORDER BY e.detected_at, e.event_id
            """, [source_run_id, since_date, eff_now.date(), self._db_timestamp(eff_now)]
        ).fetchall()
        cols = [desc[0] for desc in self.store.connection.description]
        events = [dict(zip(cols, row)) for row in rows]
        candidates: list[SectorImpactCandidate] = []
        path_metrics = {
            "events_without_causal_root": 0,
            "active_paths": 0,
            "paths_in_lag": 0,
            "expired_paths": 0,
        }
        for event in events:
            for candidate in self._propagate_event(event, eff_now, path_metrics):
                self.store.save_sector_impact_candidate(candidate.model_dump())
                candidates.append(candidate)
        snapshots = self.aggregate_sector_state(candidates, eff_now)
        for snapshot in snapshots:
            self.store.save_sector_state_snapshot(snapshot.model_dump())
        return self._summary(
            len(events), candidates, len(snapshots), source_run_id, eff_now, path_metrics
        )

    def propagate_event(self, evt: dict, as_of_timestamp: Optional[datetime] = None) -> list[SectorImpactCandidate]:
        return self._propagate_event(evt, as_of_timestamp, None)

    def _propagate_event(
        self,
        evt: dict,
        as_of_timestamp: Optional[datetime],
        metrics: Optional[dict[str, int]],
    ) -> list[SectorImpactCandidate]:
        as_of = self._utc(as_of_timestamp or datetime.now(timezone.utc))
        try:
            root = causal_root(str(evt["event_type"]), str(evt["direction"]))
        except ValueError:
            if metrics is not None:
                metrics["events_without_causal_root"] += 1
            return []
        event_strength = self._event_strength(evt)
        available_at = self._utc(evt.get("event_available_at") or evt.get("available_at") or evt["detected_at"])
        age_days = (as_of - available_at).total_seconds() / 86400.0
        if age_days < 0:
            return []
        paths = self._find_all_paths(root, set())
        if not paths:
            if metrics is not None:
                metrics["events_without_causal_root"] += 1
            return []
        sector_paths: dict[str, list[dict]] = {}
        regime = str(evt.get("current_regime", ""))
        for edges in paths:
            if any(edge.regime_conditions and regime not in edge.regime_conditions for edge in edges):
                continue
            lag = sum(edge.lag_days for edge in edges)
            horizon = min(edge.horizon_days for edge in edges)
            if age_days < lag:
                if metrics is not None:
                    metrics["paths_in_lag"] += 1
                continue
            if age_days > horizon:
                if metrics is not None:
                    metrics["expired_paths"] += 1
                continue
            if metrics is not None:
                metrics["active_paths"] += 1
            direction = math.prod(edge.direction for edge in edges)
            strength = math.prod(edge.strength for edge in edges)
            confidence = math.prod(edge.confidence for edge in edges)
            half_life = min(edge.half_life_days for edge in edges)
            decay = 0.5 ** ((age_days - lag) / max(1, half_life))
            path_strength = abs(event_strength) * strength * decay
            event_direction = -1 if event_strength < 0 else 1
            impact = path_strength * event_direction * direction * confidence
            sector = edges[-1].target_node.removeprefix("B3_SECTOR_")
            sector_paths.setdefault(sector, []).append({
                "edges": edges, "impact": impact, "direction": direction,
                "strength": path_strength, "confidence": confidence,
                "horizon": horizon,
            })
        return [self._candidate(evt, root, event_strength, available_at, as_of, sector, paths)
                for sector, paths in sector_paths.items()]

    @staticmethod
    def _event_strength(evt: dict) -> float:
        breakdown = evt.get("score_breakdown") or {}
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except (TypeError, json.JSONDecodeError):
                breakdown = {}
        effective = float(breakdown.get("effective_surprise", evt.get("surprise_score", 0.0)))
        novelty = float(evt.get("novelty_score", 0.0))
        persistence = float(evt.get("persistence_score", 0.0))
        quality = float(evt.get("data_quality_score", 0.0))
        status_weight = 1.0 if evt.get("status") == "MACRO_EVENT_APPROVED" else 0.6
        return effective * (0.6 + 0.4 * novelty) * (0.7 + 0.3 * persistence) * quality * status_weight

    def _candidate(self, evt: dict, root: str, event_strength: float, available_at: datetime,
                   as_of: datetime, sector: str, paths: list[dict]) -> SectorImpactCandidate:
        net = sum(path["impact"] for path in paths)
        signed_score = round(math.tanh(net), 4)
        confidence = round(sum(path["confidence"] for path in paths) / len(paths), 4)
        positives = sum(path["impact"] > 0 for path in paths)
        negatives = sum(path["impact"] < 0 for path in paths)
        magnitude = abs(signed_score)
        status = (SectorImpactStatus.SECTOR_IMPACT_APPROVED.value if magnitude >= 0.60 and confidence >= 0.65
                  else SectorImpactStatus.SECTOR_IMPACT_WATCH.value if magnitude >= 0.35
                  else SectorImpactStatus.SECTOR_IMPACT_REJECTED.value)
        horizon = max(path["horizon"] for path in paths)
        evidence_state = "VALIDATED" if all(
            edge.evidence_ids for path in paths for edge in path["edges"]
        ) else "HYPOTHESIS"
        if (
            evidence_state == "HYPOTHESIS"
            and status == SectorImpactStatus.SECTOR_IMPACT_APPROVED.value
        ):
            status = SectorImpactStatus.SECTOR_IMPACT_WATCH.value
        candidate_id = hashlib.sha256(
            f"{self.run_id}|{self.graph_version}|{as_of.isoformat()}|{evt['event_id']}|{sector}".encode()
        ).hexdigest()[:24]
        causal_paths = []
        for path in paths:
            edges = path["edges"]
            metadata_edges = [edge for edge in edges if edge.factor]
            if len(metadata_edges) != 1:
                raise ValueError(
                    "each causal path must declare exactly one explicit factor edge"
                )
            metadata = metadata_edges[0]
            nodes = [edges[0].source_node] + [edge.target_node for edge in edges]
            edge_ids = [edge.edge_id for edge in edges]
            evidence_ids = [
                evidence_id for edge in edges for evidence_id in edge.evidence_ids
            ]
            path_id = hashlib.sha256(
                f"{self.graph_version}|{'>'.join(edge_ids)}".encode()
            ).hexdigest()[:24]
            causal_paths.append(CausalPath(
                path_id=path_id, nodes=nodes, causal_edge_ids=edge_ids,
                factor=metadata.factor or "UNKNOWN",
                company_channel_effects=metadata.company_channel_effects,
                factor_direction=metadata.direction,
                direction=path["direction"],
                strength=round(path["strength"], 6),
                confidence=round(path["confidence"], 6),
                evidence_ids=evidence_ids,
                evidence_status=(
                    "VALIDATED" if all(edge.evidence_ids for edge in edges)
                    else "HYPOTHESIS"
                ),
            ))
        return SectorImpactCandidate(
            candidate_id=candidate_id, event_id=evt["event_id"], event_type=evt["event_type"],
            causal_root=root, sector=sector, direction="BULLISH" if net >= 0 else "BEARISH",
            impact_score=signed_score, event_strength=round(event_strength, 6), confidence=confidence,
            horizon_days=horizon, horizon_months=max(1, math.ceil(horizon / 30)),
            causal_paths=causal_paths,
            direct_effects=[p["edges"][0].rationale for p in paths if len(p["edges"]) == 1],
            second_order_effects=[p["edges"][-1].rationale for p in paths if len(p["edges"]) > 1],
            positive_paths_count=positives, negative_paths_count=negatives,
            conflict_detected=bool(positives and negatives), evidence_status=evidence_state,
            status=status, event_available_at=available_at, detected_at=as_of,
            as_of_timestamp=as_of, run_id=self.run_id, source_event_run_id=str(evt.get("ingestion_run_id", self.run_id)),
            graph_version=self.graph_version,
        )

    def aggregate_sector_state(self, candidates: list[SectorImpactCandidate], as_of: datetime) -> list[SectorStateSnapshot]:
        grouped: dict[str, list[SectorImpactCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.sector, []).append(candidate)
        results = []
        for sector, items in grouped.items():
            bullish = sum(max(0.0, item.impact_score) for item in items)
            bearish = sum(abs(min(0.0, item.impact_score)) for item in items)
            gross = bullish + bearish
            net = math.tanh(bullish - bearish)
            conflict = 0.0 if gross == 0 else min(bullish, bearish) / max(bullish, bearish)
            confidence = sum(item.confidence * abs(item.impact_score) for item in items) / gross if gross else 0.0
            status = "SECTOR_STATE_WATCH" if conflict >= 0.5 or abs(net) < 0.35 else "SECTOR_STATE_ACTIVE"
            snapshot_id = hashlib.sha256(
                f"{self.run_id}|{self.graph_version}|{as_of.isoformat()}|{sector}".encode()
            ).hexdigest()[:24]
            results.append(SectorStateSnapshot(
                snapshot_id=snapshot_id, sector=sector, as_of_timestamp=as_of,
                net_impact=round(net, 4), bullish_impact=round(bullish, 4),
                bearish_impact=round(bearish, 4), conflict_ratio=round(conflict, 4),
                supporting_event_ids=[i.event_id for i in items if i.impact_score > 0],
                opposing_event_ids=[i.event_id for i in items if i.impact_score < 0],
                confidence=round(confidence, 4), status=status,
                run_id=self.run_id, graph_version=self.graph_version,
            ))
        return results

    def _find_all_paths(self, start_node: str, visited: set[str], current_path=None) -> list[list[CausalEdge]]:
        current_path = current_path or []
        if start_node in visited:
            return []
        visited.add(start_node)
        results = []
        for edge in self.adj_list.get(start_node, []):
            path = current_path + [edge]
            if edge.target_node.startswith("B3_SECTOR_"):
                results.append(path)
            else:
                results.extend(self._find_all_paths(edge.target_node, set(visited), path))
        return results

    @staticmethod
    def _utc(value: datetime | str) -> datetime:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    @staticmethod
    def _db_timestamp(value: datetime) -> datetime:
        """DuckDB TIMESTAMP columns store UTC instants without timezone metadata."""
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def _summary(
        self,
        events: int,
        candidates: list[SectorImpactCandidate],
        snapshots: int = 0,
        macro_run_id: str = "",
        as_of: Optional[datetime] = None,
        path_metrics: Optional[dict[str, int]] = None,
    ) -> dict[str, int | str]:
        metrics = path_metrics or {
            "events_without_causal_root": 0,
            "active_paths": 0,
            "paths_in_lag": 0,
            "expired_paths": 0,
        }
        return {
            "macro_event_run_id": macro_run_id,
            "sector_run_id": self.run_id,
            "graph_version": self.graph_version,
            "as_of_timestamp": as_of.isoformat() if as_of else "",
            "macro_events_processed": events,
            **metrics,
            "validated_impacts": sum(c.evidence_status == "VALIDATED" for c in candidates),
            "hypothetical_impacts": sum(c.evidence_status == "HYPOTHESIS" for c in candidates),
            "sector_impacts_approved": sum(c.status == SectorImpactStatus.SECTOR_IMPACT_APPROVED.value for c in candidates),
            "sector_impacts_watch": sum(c.status == SectorImpactStatus.SECTOR_IMPACT_WATCH.value for c in candidates),
            "sector_impacts_rejected": sum(c.status == SectorImpactStatus.SECTOR_IMPACT_REJECTED.value for c in candidates),
            "sector_state_snapshots": snapshots,
        }
