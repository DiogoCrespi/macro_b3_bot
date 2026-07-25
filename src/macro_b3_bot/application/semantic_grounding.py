"""Controlled semantic checks between MiroFish text and PIT upstream inputs."""
from __future__ import annotations

import json
import re
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").casefold()


def _geographies(value: Any) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, list):
        value = [value] if value else []
    return {_text(item).strip() for item in value if item}


def validate_hypothesis_grounding(
    hypothesis: dict[str, Any],
    *,
    release: dict[str, Any] | None,
    claims: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    sector_states: list[dict[str, Any]],
    report_text: str,
) -> dict[str, Any]:
    """Return deterministic grounding checks; never infer missing evidence."""
    reasons: list[str] = []
    release = release or {}
    indicator = _text(release.get("indicator"))
    geography = _geographies(release.get("geography"))
    trigger = _text(hypothesis.get("trigger"))
    factors = " ".join(_text(x) for x in hypothesis.get("macro_factors", []))
    report = _text(report_text)

    declared_claims = {str(x) for x in hypothesis.get("supporting_evidence_claim_ids", []) if x}
    available_claims = {str(x.get("claim_id")) for x in claims if x.get("claim_id")}
    if declared_claims - available_claims:
        reasons.append("DECLARED_CLAIM_NOT_AVAILABLE_AT_PIT")

    declared_docs = {str(x) for x in hypothesis.get("source_document_ids", []) if x}
    available_docs = {str(x.get("document_id")) for x in documents if x.get("document_id")}
    if not declared_docs:
        reasons.append("SOURCE_DOCUMENT_IDS_MISSING_FROM_HYPOTHESIS")
    elif declared_docs - available_docs:
        reasons.append("DECLARED_SOURCE_DOCUMENT_NOT_AVAILABLE_AT_PIT")

    if "ipca" in indicator or "índice nacional" in indicator or "national consumer" in indicator:
        if any(token in trigger or token in factors for token in ("global", "全球", "global inflation")):
            reasons.append("IPCA_NATIONAL_INDEX_MAPPED_TO_GLOBAL_INFLATION")
        if geography and not ({"br", "brazil", "brasil"} & geography):
            reasons.append("IPCA_GEOGRAPHY_NOT_BRAZIL")
        if not any(token in trigger or token in factors for token in ("ipca", "nacional", "brasil", "brazil", "consumer")):
            reasons.append("IPCA_FACTOR_NOT_EXPLICITLY_PRESERVED")

    # ITR is a controlled CVM acronym. A translation to information technology
    # is a source-semantic contradiction, not a harmless language variation.
    if "itr" in report and any(token in report for token in ("information technology report", "信息技术报告")):
        reasons.append("CVM_ITR_MAPPED_TO_INFORMATION_TECHNOLOGY_REPORT")

    if hypothesis.get("macro_event_ids") and not release:
        reasons.append("MACRO_EVENT_NOT_RESOLVED_EXACTLY")
    if hypothesis.get("sector_state_ids") and not sector_states:
        reasons.append("SECTOR_STATE_NOT_RESOLVED_AT_PIT")

    # Basic controlled sector compatibility; absence is unknown, never a match.
    sector_text = " ".join(_text(s.get("sector")) for s in sector_states)
    effects = " ".join(_text(x) for x in hypothesis.get("sector_effects", []))
    if "retail" in sector_text and not re.search(r"retail|varejo|零售", effects):
        reasons.append("SECTOR_EFFECT_NOT_COMPATIBLE_WITH_RETAIL_STATE")

    return {
        "status": "REJECTED_SEMANTIC_SOURCE_MISMATCH" if reasons else "SEMANTICALLY_GROUNDED",
        "reasons": sorted(set(reasons)),
        "indicator": release.get("indicator"),
        "unit": release.get("unit"),
        "geography": sorted(geography),
        "claim_ids_checked": sorted(declared_claims),
        "document_ids_checked": sorted(declared_docs),
    }
