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
                as_of_timestamp = as_of_timestamp.replace(tzinfo=timezone.utc)
            as_of_str = as_of_timestamp.isoformat()
        else:
            as_of_str = str(as_of_timestamp)

        critical_blockers: list[str] = []
        noncritical_warnings: list[str] = []
        missing_inputs: list[str] = []
        invalidation_conditions: list[str] = []

        # 1. Macro events & directional conflicts
        macro_event_ids = [e.get("macro_event_id") or e.get("event_id") for e in macro_events if e.get("macro_event_id") or e.get("event_id")]
        active_factors = list({e.get("factor") for e in macro_events if e.get("factor")})
        factor_directions = {}
        has_conflict = False
        for e in macro_events:
            factor = e.get("factor")
            direction = e.get("factor_direction", 1)
            if factor:
                if factor in factor_directions and factor_directions[factor] != direction:
                    has_conflict = True
                factor_directions[factor] = direction
        
        macro_conflict_status = "NO_CONFLICT"
        if has_conflict or any(c.get("decision_mode_status") == "BLOCKED" for c in macro_events):
            has_conflict = True
            macro_conflict_status = "CONFLICTING_MACRO_DIRECTION"
            critical_blockers.append("CONFLICTING_MACRO_DIRECTION")
            invalidation_conditions.append("Unresolved conflicting macro factor directions detected")

        # 2. Sector signal evaluation
        sector_active = False
        if sector_state:
            sector_active = bool(sector_state.get("is_active", False) or sector_state.get("impact_score", 0.0) != 0.0 or sector_state.get("has_active_signal", False))
        if not sector_active:
            critical_blockers.append("NO_ACTIVE_SECTOR_SIGNAL")
            invalidation_conditions.append("No active macro/sector signal for company's sector")

        # 3. Company exposure evaluation
        approved_exposures = [c for c in company_contributions if c.get("approval_status") in ("HUMAN_APPROVED", "DELEGATED_AI_APPROVED", "APPROVED") or c.get("is_approved", False)]
        if not approved_exposures:
            critical_blockers.append("NO_APPROVED_COMPANY_EXPOSURE")
            invalidation_conditions.append("No approved company macro exposure ingested")

        # 4. Financial channel calculability
        calculable_channels = [f for f in financial_outcomes if f.get("status") in ("CALCULATED", "PARTIAL") and f.get("financial_outcome_id")]
        if not calculable_channels and approved_exposures:
            critical_blockers.append("NO_CALCULABLE_FINANCIAL_CHANNEL")
            invalidation_conditions.append("No calculable financial bridge channel for approved exposures")

        # 5. Security & PIT integrity checks
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

        # 8. Decision logic
        if critical_blockers:
            decision = "NO_ACTION"
        elif sector_active and approved_exposures and (calculable_channels or financial_outcomes):
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

        # Penalize confidence for non-critical warnings
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
            blocker_str = ", ".join(critical_blockers) if critical_blockers else "insufficient active signals"
            rationale_parts.append(
                f"Company {ticker} assigned NO_ACTION due to critical blockers: [{blocker_str}]."
            )

        if noncritical_warnings:
            rationale_parts.append(f"Non-critical warnings noted: [{', '.join(noncritical_warnings)}].")

        if historical_multiple_position.get("summary"):
            rationale_parts.append(f"Valuation Context: {historical_multiple_position['summary']}")

        rationale = " ".join(rationale_parts)

        # 11. Build payload & content hash
        payload_data = {
            "ticker": ticker,
            "as_of_timestamp": as_of_str,
            "decision": decision,
            "critical_blockers": sorted(critical_blockers),
            "noncritical_warnings": sorted(noncritical_warnings),
            "macro_event_ids": sorted(macro_event_ids),
            "confidence": confidence,
            "input_ids": input_ids,
        }
        decision_id = ResearchDecisionSnapshot.compute_decision_id(payload_data)

        return ResearchDecisionSnapshot(
            decision_id=decision_id,
            ticker=ticker,
            as_of_timestamp=as_of_str,
            decision=decision,
            macro_event_ids=macro_event_ids,
            active_factors=active_factors,
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
            critical_blockers=critical_blockers,
            noncritical_warnings=noncritical_warnings,
            missing_inputs=missing_inputs,
            rationale=rationale,
            invalidation_conditions=invalidation_conditions,
            methodology_version=self.methodology_version,
            input_ids=input_ids,
        )
