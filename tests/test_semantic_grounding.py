from macro_b3_bot.application.semantic_grounding import validate_hypothesis_grounding


def _hypothesis(trigger="IPCA nacional de 0,45%", factors=None, docs=None):
    return {
        "macro_event_ids": ["rel-1"],
        "supporting_evidence_claim_ids": ["claim-1"],
        "source_document_ids": docs if docs is not None else ["doc-1"],
        "sector_state_ids": ["sector-1"],
        "trigger": trigger,
        "macro_factors": factors if factors is not None else ["IPCA nacional"],
        "sector_effects": ["varejo com pressão de custos"],
    }


def test_global_inflation_cannot_bind_to_brazilian_ipca():
    result = validate_hypothesis_grounding(
        _hypothesis(trigger="Global inflation rises to 0.45%", factors=["global inflation"]),
        release={"indicator": "IPCA", "unit": "%", "geography": '["BR"]'},
        claims=[{"claim_id": "claim-1"}],
        documents=[{"document_id": "doc-1"}],
        sector_states=[{"snapshot_id": "sector-1", "sector": "RETAIL"}],
        report_text="global inflation 0.45%",
    )
    assert result["status"] == "REJECTED_SEMANTIC_SOURCE_MISMATCH"
    assert "IPCA_NATIONAL_INDEX_MAPPED_TO_GLOBAL_INFLATION" in result["reasons"]


def test_numeric_overlap_alone_is_not_a_claim_binding():
    result = validate_hypothesis_grounding(
        _hypothesis(docs=[]),
        release={"indicator": "IPCA", "unit": "%", "geography": '["BR"]'},
        claims=[],
        documents=[],
        sector_states=[{"snapshot_id": "sector-1", "sector": "RETAIL"}],
        report_text="0.45%",
    )
    assert result["status"] == "REJECTED_SEMANTIC_SOURCE_MISMATCH"
    assert "DECLARED_CLAIM_NOT_AVAILABLE_AT_PIT" in result["reasons"]
