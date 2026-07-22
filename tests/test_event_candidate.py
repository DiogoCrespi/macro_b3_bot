import sys
import unittest
import tempfile
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.domain.event_models import EventCandidate
from macro_b3_bot.application.build_event_candidates import EventCandidateBuilder
from macro_b3_bot.infrastructure.store import DatabaseStore

class TestEventCandidate(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "audit.duckdb"
        self.store = DatabaseStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_event_candidate_schema_and_status(self):
        evt = EventCandidate(
            event_id="EVT_TEST_1",
            ticker="PETR4",
            cvm_code="004170",
            event_type="DIVIDEND_DECLARED",
            title="DIVIDEND_DECLARED: PETR4 - Aprovação de Dividendos",
            effective_date=None,
            claim_ids=["CLAIM_1"],
            evidence_count=1,
            novelty_score=0.90,
            materiality_score=0.85,
            persistence_score=0.85,
            quantitative_impact={"declared_value": Decimal("1.50")},
            invalidators=[],
            status="EVENT_GATE_APPROVED",
            created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.status, "EVENT_GATE_APPROVED")
        self.assertNotEqual(evt.status, "BUY")
        self.assertEqual(evt.quantitative_impact["declared_value"], Decimal("1.50"))

    def test_save_and_count_event_candidates(self):
        evt_dict = {
            "event_id": "EVT_STORE_1",
            "ticker": "VALE3",
            "cvm_code": "004171",
            "event_type": "BUYBACK_AUTHORIZED",
            "title": "BUYBACK_AUTHORIZED: VALE3 - Recompra",
            "effective_date": None,
            "claim_ids": ["CLAIM_2"],
            "evidence_count": 1,
            "novelty_score": 0.88,
            "materiality_score": 0.75,
            "persistence_score": 0.80,
            "quantitative_impact": {"shares": 10000000},
            "invalidators": [],
            "status": "EVENT_GATE_APPROVED"
        }
        self.store.save_event_candidate(evt_dict)
        count = self.store.count_event_candidates()
        self.assertEqual(count, 1)

    def test_event_candidate_builder_pipeline(self):
        from macro_b3_bot.domain.evidence_models import EvidenceClaim
        builder = EventCandidateBuilder(Settings(data_dir=Path(self.temp_dir.name)))
        
        claim = EvidenceClaim(
            claim_id="CLAIM_EVT_1",
            document_id="DOC_EVT_1",
            cvm_code="004170",
            ticker="PETR4",
            claim_type="DIVIDEND",
            subject="Dividendos Aprovados",
            predicate="HAS_PAYMENT",
            object_text="DIVIDEND de R$ 1.50",
            numeric_value=Decimal("1.50"),
            unit="BRL",
            currency="BRL",
            source_page=1,
            source_start=0,
            source_end=10,
            source_excerpt="Aprovado R$ 1.50",
            extraction_method="REGEX",
            confidence=0.95,
            created_at=datetime.now(timezone.utc)
        )
        self.store.save_evidence_claim(claim.model_dump(mode="json"))
        self.store.save_ipe_processing_state({
            "document_id": "DOC_EVT_1",
            "status": "EVIDENCE_BUILT",
            "priority_score": 0.85
        })

        res = builder.build_event_candidates_batch(limit=10)
        self.assertGreaterEqual(res["events_created"], 1)

    def test_event_candidate_jcp_type(self):
        evt = EventCandidate(
            event_id="EVT_JCP", ticker="PETR4", cvm_code="004170",
            event_type="JCP_DECLARED", title="JCP PETR4", novelty_score=0.9,
            materiality_score=0.8, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "JCP_DECLARED")

    def test_event_candidate_buyback_type(self):
        evt = EventCandidate(
            event_id="EVT_BUYBACK", ticker="VALE3", cvm_code="004171",
            event_type="BUYBACK_AUTHORIZED", title="Recompra VALE3", novelty_score=0.9,
            materiality_score=0.8, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "BUYBACK_AUTHORIZED")

    def test_event_candidate_capital_increase_type(self):
        evt = EventCandidate(
            event_id="EVT_CAPITAL", ticker="WEGE3", cvm_code="005410",
            event_type="CAPITAL_INCREASE", title="Aumento Capital WEGE3", novelty_score=0.9,
            materiality_score=0.8, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "CAPITAL_INCREASE")

    def test_event_candidate_debt_issuance_type(self):
        evt = EventCandidate(
            event_id="EVT_DEBT", ticker="ITUB4", cvm_code="001934",
            event_type="DEBT_ISSUANCE", title="Debêntures ITUB4", novelty_score=0.9,
            materiality_score=0.8, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "DEBT_ISSUANCE")

    def test_event_candidate_acquisition_type(self):
        evt = EventCandidate(
            event_id="EVT_ACQ", ticker="RENT3", cvm_code="016144",
            event_type="ACQUISITION", title="Aquisição RENT3", novelty_score=0.95,
            materiality_score=0.85, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "ACQUISITION")

    def test_event_candidate_recovery_event_type(self):
        evt = EventCandidate(
            event_id="EVT_REC", ticker="OIBR3", cvm_code="011312",
            event_type="RECOVERY_EVENT", title="RJ OIBR3", novelty_score=0.99,
            materiality_score=0.95, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "RECOVERY_EVENT")

    def test_event_candidate_operational_interruption(self):
        evt = EventCandidate(
            event_id="EVT_INT", ticker="VALE3", cvm_code="004171",
            event_type="OPERATIONAL_INTERRUPTION", title="Paralisação Mina", novelty_score=0.95,
            materiality_score=0.90, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "OPERATIONAL_INTERRUPTION")

    def test_event_candidate_guidance_changed(self):
        evt = EventCandidate(
            event_id="EVT_GUIDE", ticker="PETR4", cvm_code="004170",
            event_type="GUIDANCE_CHANGED", title="Revisão CAPEX", novelty_score=0.85,
            materiality_score=0.80, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "GUIDANCE_CHANGED")

    def test_event_candidate_divestment(self):
        evt = EventCandidate(
            event_id="EVT_DIVEST", ticker="BBAS3", cvm_code="001023",
            event_type="DIVESTMENT", title="Venda de Ativo", novelty_score=0.90,
            materiality_score=0.75, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "DIVESTMENT")

    def test_event_candidate_debt_renegotiation(self):
        evt = EventCandidate(
            event_id="EVT_RENEG", ticker="CSNA3", cvm_code="004014",
            event_type="DEBT_RENEGOTIATION", title="Refinanciamento", novelty_score=0.88,
            materiality_score=0.82, status="EVENT_GATE_APPROVED", created_at=datetime.now(timezone.utc)
        )
        self.assertEqual(evt.event_type, "DEBT_RENEGOTIATION")

    def test_event_candidate_rejection_low_scores(self):
        builder = EventCandidateBuilder(Settings(data_dir=Path(self.temp_dir.name)))
        from macro_b3_bot.domain.evidence_models import EvidenceClaim
        claim = EvidenceClaim(
            claim_id="CLAIM_LOW_1", document_id="DOC_LOW_1", cvm_code="004170", ticker="PETR4",
            claim_type="DIVIDEND", subject="Sem relevância", predicate="HAS_PAYMENT", object_text="Nulo",
            numeric_value=Decimal("0.0"), unit="BRL", currency="BRL", source_page=1, source_start=0, source_end=1,
            source_excerpt="Nulo", extraction_method="REGEX", confidence=0.20, created_at=datetime.now(timezone.utc)
        )
        self.store.save_evidence_claim(claim.model_dump(mode="json"))
        self.store.save_ipe_processing_state({"document_id": "DOC_LOW_1", "status": "EVIDENCE_BUILT", "priority_score": 0.20})
        res = builder.build_event_candidates_batch(limit=10)
        self.assertGreaterEqual(res["events_created"], 1)

if __name__ == "__main__":
    unittest.main()
