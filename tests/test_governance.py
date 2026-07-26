import json

import pytest

from macro_b3_bot.application.governance import AppendOnlyAuditLedger, GovernanceActor, KillSwitch, require_permission


def test_governance_requires_authenticated_role():
    with pytest.raises(PermissionError):
        require_permission(GovernanceActor("operator-1", "operator"), "run_pipeline")
    require_permission(GovernanceActor("operator-1", "operator", authenticated=True), "run_pipeline")


def test_audit_ledger_hash_chain_is_append_only(tmp_path):
    ledger = AppendOnlyAuditLedger(tmp_path / "audit.jsonl")
    actor = GovernanceActor("reviewer-1", "reviewer", authenticated=True)
    ledger.append(actor=actor, action="REVIEW", payload={"fact_id": "f1"})
    ledger.append(actor=actor, action="BINDING", payload={"hypothesis_id": "h1"})
    assert ledger.verify() == (True, "VALID")
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[1])["previous_hash"] == json.loads(lines[0])["entry_hash"]
    (tmp_path / "audit.jsonl").write_text(lines[0] + "\n" + lines[1].replace("BINDING", "TAMPERED"), encoding="utf-8")
    assert ledger.verify()[0] is False


def test_kill_switch_requires_admin_and_persists(tmp_path):
    switch = KillSwitch(tmp_path / "kill-switch.json")
    with pytest.raises(PermissionError):
        switch.activate(GovernanceActor("reviewer-1", "reviewer", authenticated=True), "stop")
    switch.activate(GovernanceActor("admin-1", "administrator", authenticated=True), "incident")
    assert switch.is_active()
