"""Sprint 4E.3 Research Decision Synthesis Application Service.

Technical Debt Registration (Sprint 4E.2 Frozen Baseline):
1. PITSecurityMapping in DuckDB is classified as RECONSTRUCTED_VALIDATED_MAPPING, not strict contemporaneous PIT.
2. source_retrieved_at prefers acquisition manifest timestamps over datetime.now(timezone.utc).
3. SUZB3 share scale adjustment is classified as PILOT_COMPANY_RECONCILIATION_HEURISTIC.

This module synthesizes macro, sector, company exposure, financial bridge, calibration,
and historical valuation context into an auditable ResearchDecisionSnapshot.

Outputs are strictly WATCH or NO_ACTION. DCF valuation, target prices, BUY recommendations,
MiroFish scenarios, and order execution remain STRICTLY BLOCKED.
"""

from datetime import datetime, timezone
from typing import Any
from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot


class ResearchDecisionSynthesizer:
    """
    Synthesizes upstream audit snapshots into a deterministic ResearchDecisionSnapshot.
    """
    methodology_version = "4E.3-research-decision-synthesis-v1"

    CRITICAL_GATES = {
        "NO_ACTIVE_SECTOR_SIGNAL",
        "CONFLICTING_MACRO_DIRECTION",
        "MACRO_EVENT_BLOCKED",
        "NO_APPROVED_COMPANY_EXPOSURE",
        "NO_CALCULABLE_FINANCIAL_CHANNEL",
        "LOOKAHEAD_OR_PIT_FAILURE",
        "MARKET_SECURITY_MISMATCH",
    }

    NONCRITICAL_WARNINGS = {
        "FCF_NOT_DCF_READY",
        "EMPIRICAL_CALIBRATION_INCOMPLETE",
        "SMALL_VALUATION_SAMPLE",
    }

    def synthesize(
        self,
        *,
        ticker: str,
        as_of_timestamp: datetime | str,
        macro_events: list[dict[str, Any]] | None = None,
        sector_state: dict[str, Any] | None = None,
        company_contributions: list[dict[str, Any]] | None = None,
        financial_outcomes: list[dict[str, Any]] | None = None,
        calibration_results: list[dict[str, Any]] | None = None,
        valuation_assessment: dict[str, Any] | None = None,
        historical_multiple_position: dict[str, Any] | None = None,
        price_implied_fundamentals: dict[str, Any] | None = None,
        security_mapping: dict[str, Any] | None = None,
        execution_mode: str = "BLOCKED_MISSING_UPSTREAM_INPUT",
        input_ids: dict[str, Any] | None = None,
    ) -> ResearchDecisionSnapshot:
        macro_events = macro_events or []
        company_contributions = company_contributions or []
        financial_outcomes = financial_outcomes or []
        calibration_results = calibration_results or []
        valuation_assessment = valuation_assessment or {}
        historical_multiple_position = historical_multiple_position or {}
        price_implied_fundamentals = price_implied_fundamentals or {}
        input_ids = input_ids or {}

        if isinstance(as_of_timestamp, datetime):
            if as_of_timestamp.tzinfo is None:
                as_of_dt = as_of_timestamp.replace(tzinfo=timezone.utc)
            else:
                as_of_dt = as_of_timestamp.astimezone(timezone.utc)
            as_of_str = as_of_dt.isoformat()
        else:
            as_of_str = str(as_of_timestamp)
            as_of_dt = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
            if as_of_dt.tzinfo is None:
                as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

        critical_blockers: list[str] = []
        noncritical_warnings: list[str] = []
        missing_inputs: list[str] = []
        invalidation_conditions: list[str] = []

        # 1. Macro events & directional conflicts
        macro_event_ids = [
            str(e.get("macro_event_id") or e.get("event_id"))
            for e in macro_events
            if e.get("macro_event_id") or e.get("event_id")
        ]
        active_factors = list({str(e.get("factor")) for e in macro_events if e.get("factor")})
        
        factor_direction_sets: dict[str, set[int]] = {}
        factor_directions: dict[str, Any] = {}
        has_directional_conflict = False
        has_blocked_macro_event = False

        for e in macro_events:
            factor = e.get("factor")
            direction = e.get("factor_direction")
            status = e.get("decision_mode_status") or e.get("status")
            
            if status == "BLOCKED" and direction is None:
                has_blocked_macro_event = True

            if factor and direction is not None:
                factor_direction_sets.setdefault(factor, set()).add(int(direction))

        for factor, dirs in factor_direction_sets.items():
            if len(dirs) > 1:
                has_directional_conflict = True
                factor_directions[factor] = None
            elif len(dirs) == 1:
                factor_directions[factor] = next(iter(dirs))

        macro_conflict_status = "NO_CONFLICT"
        if has_directional_conflict:
            macro_conflict_status = "CONFLICTING_MACRO_DIRECTION"
            critical_blockers.append("CONFLICTING_MACRO_DIRECTION")
            invalidation_conditions.append("Unresolved opposing macro factor directions detected")
        elif has_blocked_macro_event:
            critical_blockers.append("MACRO_EVENT_BLOCKED")
            invalidation_conditions.append("Macro event blocked for non-directional reasons")

        # 2. Sector signal evaluation (Strict: BOTH is_active and has_active_signal must be True)
        sector_active = False
        if sector_state:
            is_act = bool(sector_state.get("is_active", False))
            has_sig = bool(sector_state.get("has_active_signal", False))
            if is_act and has_sig:
                sector_active = True

        if not sector_active:
            critical_blockers.append("NO_ACTIVE_SECTOR_SIGNAL")
            invalidation_conditions.append("No active macro/sector signal for company's sector")

        # 3. Company exposure evaluation (Strict: contribution_id and channel must be non-null)
        approved_exposures = [
            c for c in company_contributions
            if (c.get("approval_status") in ("HUMAN_APPROVED", "DELEGATED_AI_APPROVED", "APPROVED")
                or c.get("is_approved", False))
            and c.get("contribution_id") is not None
            and c.get("channel") is not None
        ]
        if not approved_exposures:
            critical_blockers.append("NO_APPROVED_COMPANY_EXPOSURE")
            invalidation_conditions.append("No approved company macro exposure with valid ID and channel ingested")

        # 4. Financial channel calculability (Strict: requires finite numeric delta and non-null IDs)
        import math

        def is_calculable(outcome: dict[str, Any]) -> bool:
            status = outcome.get("status")
            if status not in ("CALCULATED", "PARTIAL"):
                return False
            if not outcome.get("financial_outcome_id"):
                return False
            numeric_fields = ["delta_net_income", "delta_ebitda", "delta_fcf", "calculated_value", "delta_revenue"]
            for f in numeric_fields:
                val = outcome.get(f)
                if val is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
                    if math.isfinite(val):
                        return True
            return False

        calculable_channels = [f for f in financial_outcomes if is_calculable(f)]
        if not calculable_channels and approved_exposures:
            critical_blockers.append("NO_CALCULABLE_FINANCIAL_CHANNEL")
            invalidation_conditions.append("No calculable financial bridge channel with finite numeric output for approved exposures")

        # 5. Direct PIT & Security Integrity Checks
        temporal_keys = [
            "available_at", "event_available_at", "document_available_at",
            "price_available_at", "share_count_available_at", "mapping_available_at",
            "baseline_available_at", "created_from_data_available_at", "as_of_timestamp"
        ]

        def check_pit(item: dict[str, Any]) -> bool:
            found_ts = False
            for tk in temporal_keys:
                avail = item.get(tk)
                if avail is not None:
                    found_ts = True
                    if isinstance(avail, str):
                        avail_dt = datetime.fromisoformat(avail.replace("Z", "+00:00"))
                    elif isinstance(avail, datetime):
                        avail_dt = avail
                    else:
                        continue
                    if avail_dt.tzinfo is None:
                        avail_dt = avail_dt.replace(tzinfo=timezone.utc)
                    if avail_dt > as_of_dt:
                        return False
            # Mandatory availability timestamp check for macro events, exposures, outcomes
            if not found_ts and item.get("require_timestamp", True):
                return False
            return True

        all_inputs = (
            [dict(e, require_timestamp=True) for e in macro_events]
            + ([dict(sector_state, require_timestamp=True)] if sector_state else [])
            + [dict(c, require_timestamp=True) for c in company_contributions]
            + [dict(f, require_timestamp=True) for f in financial_outcomes]
        )

        if any(not check_pit(inp) for inp in all_inputs):
            critical_blockers.append("LOOKAHEAD_OR_PIT_FAILURE")
            invalidation_conditions.append("Input timestamp is missing or occurs after assessment as_of_timestamp")

        if valuation_assessment.get("security_mismatch") or any(
            c.get("ticker") and c.get("ticker") != ticker for c in company_contributions
        ):
            critical_blockers.append("MARKET_SECURITY_MISMATCH")
            invalidation_conditions.append("Security/ticker mismatch detected in upstream inputs")

        if valuation_assessment.get("blockers"):
            for b in valuation_assessment["blockers"]:
                if b in self.CRITICAL_GATES and b not in critical_blockers:
                    critical_blockers.append(b)
                elif b in self.NONCRITICAL_WARNINGS and b not in noncritical_warnings:
                    noncritical_warnings.append(b)

        # 6. Non-critical warnings (affect confidence, do NOT block WATCH)
        if valuation_assessment.get("fcf_dcf_eligible") is False or valuation_assessment.get("fcf_status") == "NOT_VALUATION_READY":
            if "FCF_NOT_DCF_READY" not in noncritical_warnings:
                noncritical_warnings.append("FCF_NOT_DCF_READY")

        if any(c.get("calibration_status") != "COMPANY_CALIBRATED" for c in calibration_results) or not calibration_results:
            if "EMPIRICAL_CALIBRATION_INCOMPLETE" not in noncritical_warnings:
                noncritical_warnings.append("EMPIRICAL_CALIBRATION_INCOMPLETE")

        obs_count = historical_multiple_position.get("observation_count", 0)
        if obs_count > 0 and obs_count < 10:
            if "SMALL_VALUATION_SAMPLE" not in noncritical_warnings:
                noncritical_warnings.append("SMALL_VALUATION_SAMPLE")

        # 7. Check for missing input components
        if not macro_events:
            missing_inputs.append("macro_events")
        if not sector_state:
            missing_inputs.append("sector_state")
        if not company_contributions:
            missing_inputs.append("company_contributions")
        if not financial_outcomes:
            missing_inputs.append("financial_outcomes")
        if not historical_multiple_position:
            missing_inputs.append("historical_multiple_position")

        # 8. Decision logic (Absolute Rule: execution_mode != REAL_UPSTREAM_SYNTHESIS or 0 macro_events or critical_blockers strictly forces NO_ACTION)
        if execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            if execution_mode not in critical_blockers:
                critical_blockers.append(execution_mode)
            decision = "NO_ACTION"
        elif critical_blockers or not macro_events:
            if not macro_events and "BLOCKED_MISSING_UPSTREAM_INPUT" not in critical_blockers:
                critical_blockers.append("BLOCKED_MISSING_UPSTREAM_INPUT")
            decision = "NO_ACTION"
        elif sector_active and approved_exposures and calculable_channels:
            decision = "WATCH"
        else:
            decision = "NO_ACTION"

        # 9. Confidence and tier calculation
        total_possible_inputs = 5
        present_inputs = total_possible_inputs - len(missing_inputs)
        evidence_completeness = round(present_inputs / total_possible_inputs, 2)

        base_confidence = 0.85 if decision == "WATCH" else 0.50
        if approved_exposures:
            avg_exp_conf = sum(float(c.get("confidence", 0.5)) for c in approved_exposures) / len(approved_exposures)
            base_confidence = base_confidence * 0.5 + avg_exp_conf * 0.5

        confidence = base_confidence
        if "FCF_NOT_DCF_READY" in noncritical_warnings:
            confidence *= 0.80
        if "EMPIRICAL_CALIBRATION_INCOMPLETE" in noncritical_warnings:
            confidence *= 0.75
        if "SMALL_VALUATION_SAMPLE" in noncritical_warnings:
            confidence *= 0.85
        if len(missing_inputs) > 0:
            confidence *= (1.0 - 0.10 * len(missing_inputs))

        confidence = round(max(0.01, min(1.0, confidence)), 4)

        if confidence >= 0.75:
            confidence_tier = "HIGH"
        elif confidence >= 0.40:
            confidence_tier = "MEDIUM"
        else:
            confidence_tier = "LOW"

        # 10. Rationale formulation
        rationale_parts = []
        if decision == "WATCH":
            rationale_parts.append(
                f"Company {ticker} qualified for WATCH list: active sector signal, {len(approved_exposures)} approved exposures, "
                f"and {len(calculable_channels)} calculable financial channels without critical blockers."
            )
        else:
            blocker_str = ", ".join(sorted(critical_blockers)) if critical_blockers else "insufficient active signals"
            rationale_parts.append(
                f"Company {ticker} assigned NO_ACTION due to critical blockers: [{blocker_str}]."
            )

        if noncritical_warnings:
            rationale_parts.append(f"Non-critical warnings noted: [{', '.join(sorted(noncritical_warnings))}].")

        if historical_multiple_position.get("summary"):
            rationale_parts.append(f"Valuation Context: {historical_multiple_position['summary']}")

        rationale = " ".join(rationale_parts)

        # 11. Full Canonical Payload Hashing
        full_payload_data = {
            "ticker": ticker,
            "as_of_timestamp": as_of_str,
            "decision": decision,
            "macro_event_ids": sorted(macro_event_ids),
            "active_factors": sorted(active_factors),
            "factor_directions": factor_directions,
            "macro_conflict_status": macro_conflict_status,
            "sector_state": sector_state,
            "sector_impact": sector_state.get("impact_summary") if sector_state else None,
            "company_contributions": company_contributions,
            "financial_outcomes": financial_outcomes,
            "historical_multiple_position": historical_multiple_position,
            "price_implied_fundamentals": price_implied_fundamentals,
            "valuation_classification": valuation_assessment.get("classification", "VALUATION_BLOCKED"),
            "evidence_completeness": evidence_completeness,
            "confidence": confidence,
            "confidence_tier": confidence_tier,
            "critical_blockers": sorted(critical_blockers),
            "noncritical_warnings": sorted(noncritical_warnings),
            "missing_inputs": sorted(missing_inputs),
            "rationale": rationale,
            "invalidation_conditions": sorted(invalidation_conditions),
            "methodology_version": self.methodology_version,
            "execution_mode": execution_mode,
            "input_ids": input_ids,
        }
        decision_id = ResearchDecisionSnapshot.compute_decision_id(full_payload_data)

        return ResearchDecisionSnapshot(
            decision_id=decision_id,
            ticker=ticker,
            as_of_timestamp=as_of_str,
            decision=decision,
            macro_event_ids=sorted(macro_event_ids),
            active_factors=sorted(active_factors),
            factor_directions=factor_directions,
            macro_conflict_status=macro_conflict_status,
            sector_state=sector_state,
            sector_impact=sector_state.get("impact_summary") if sector_state else None,
            company_contributions=company_contributions,
            financial_outcomes=financial_outcomes,
            historical_multiple_position=historical_multiple_position,
            price_implied_fundamentals=price_implied_fundamentals,
            valuation_classification=valuation_assessment.get("classification", "VALUATION_BLOCKED"),
            evidence_completeness=evidence_completeness,
            confidence=confidence,
            confidence_tier=confidence_tier,
            critical_blockers=sorted(critical_blockers),
            noncritical_warnings=sorted(noncritical_warnings),
            missing_inputs=sorted(missing_inputs),
            rationale=rationale,
            invalidation_conditions=sorted(invalidation_conditions),
            methodology_version=self.methodology_version,
            execution_mode=execution_mode,
            input_ids=input_ids,
        )
