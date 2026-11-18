import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import ticket

import triage_agent
from triage_agent.gate import Gate, GateError
from triage_agent.llm import FakeLLM
from triage_agent.models import (
    ALLOWED_TRANSITIONS,
    FINAL_STATES,
    OPEN_STATES,
    Case,
    CaseState,
    ClassifierResult,
    InvalidTransition,
    text_hash,
)

SRC = Path(triage_agent.__file__).parent


def drafted_env(make_env):
    env = make_env(llm=FakeLLM(script=[ClassifierResult(label="general_question", confidence=0.95)]))
    case = env.run(ticket("How do I download my invoice?"))
    assert case.state == CaseState.DRAFTED
    return env, case


def install_spy(env):
    """Every send_reply must be preceded by an `approved` row for the same case and draft hash."""
    original = env.source.send_reply
    checked = []

    def spy(ticket_id, body):
        case = next(c for c in env.store.list() if c.ticket_id == ticket_id)
        approvals = [e for e in env.audit.events(case.id) if e.event == "approved"]
        assert approvals, "send_reply called without an approved audit row"
        assert approvals[-1].case_id == case.id
        assert approvals[-1].payload["draft_hash"] == text_hash(body)
        assert "sent" not in [e.event for e in env.audit.events(case.id)]
        checked.append(ticket_id)
        original(ticket_id, body)

    env.source.send_reply = spy
    return checked


def test_approve_sends_after_recorded_approval(make_env):
    env, case = drafted_env(make_env)
    checked = install_spy(env)
    sent = env.gate.approve(case.id, "rev-1", case.draft_hash)
    assert checked == [case.ticket_id]
    assert env.source.sent == [(case.ticket_id, case.draft)]
    assert sent.state == CaseState.SENT and sent.reviewer_id == "rev-1"
    events = env.audit.events(case.id)
    assert [e.event for e in events][-2:] == ["approved", "sent"]
    assert events[-2].actor == "rev-1"


@pytest.mark.parametrize("reviewer", ["", "   ", None])
def test_approval_needs_reviewer_id(make_env, reviewer):
    env, case = drafted_env(make_env)
    with pytest.raises(GateError):
        env.gate.approve(case.id, reviewer, case.draft_hash)
    assert env.source.sent == []


def test_stale_hash_is_rejected(make_env):
    env, case = drafted_env(make_env)
    old_hash = case.draft_hash
    env.gate.edit(case.id, "rev-1", "Hello, edited reply.")
    with pytest.raises(GateError):
        env.gate.approve(case.id, "rev-1", old_hash)
    with pytest.raises(GateError):
        env.gate.approve(case.id, "rev-1", "0" * 64)
    assert env.source.sent == []


def test_escalated_and_needs_person_cases_cannot_be_approved(make_env):
    env = make_env(llm=FakeLLM(script=[ClassifierResult(label="fee_question", confidence=0.1)]))
    escalated = env.run(ticket("fraud on my card", id="A"))
    unclear = env.run(ticket("hm", id="B"))
    assert (escalated.state, unclear.state) == (CaseState.ESCALATED, CaseState.NEEDS_PERSON)
    for case in (escalated, unclear):
        with pytest.raises(GateError):
            env.gate.approve(case.id, "rev-1", case.draft_hash or text_hash(""))
    assert env.source.sent == []
    assert "approved" not in env.events()


def test_second_approval_is_rejected(make_env):
    env, case = drafted_env(make_env)
    env.gate.approve(case.id, "rev-1", case.draft_hash)
    with pytest.raises(GateError):
        env.gate.approve(case.id, "rev-2", case.draft_hash)
    assert len(env.source.sent) == 1


def test_edit_changes_hash_and_is_logged(make_env):
    env, case = drafted_env(make_env)
    edited = env.gate.edit(case.id, "rev-1", "Hello Test Kunde,\r\nhere is your invoice link.\r\n")
    assert edited.state == CaseState.DRAFTED and edited.edited
    assert edited.draft_hash != case.draft_hash
    assert edited.draft_hash == text_hash("Hello Test Kunde,\nhere is your invoice link.\n")  # CRLF-insensitive
    row = env.audit.events(case.id)[-1]
    assert (row.event, row.actor) == ("edited", "rev-1")
    assert row.payload == {"old_hash": case.draft_hash, "new_hash": edited.draft_hash}
    checked = install_spy(env)
    env.gate.approve(case.id, "rev-1", edited.draft_hash)
    assert checked and env.source.sent[0][1] == edited.draft


def test_edit_requires_reviewer_and_body(make_env):
    env, case = drafted_env(make_env)
    with pytest.raises(GateError):
        env.gate.edit(case.id, "", "x")
    with pytest.raises(GateError):
        env.gate.edit(case.id, "rev-1", "  ")


def test_take_escalated_case(make_env):
    env = make_env()
    case = env.run(ticket("This is fraud"))
    taken = env.gate.take(case.id, "rev-9")
    assert taken.state == CaseState.TAKEN and taken.reviewer_id == "rev-9"
    assert env.events(case.id)[-1] == "taken"
    with pytest.raises(GateError):
        env.gate.take(case.id, "rev-9")
    with pytest.raises(GateError):
        env.gate.approve(case.id, "rev-9", "x")
    assert env.source.sent == []


def test_take_rejects_drafted_case(make_env):
    env, case = drafted_env(make_env)
    with pytest.raises(GateError):
        env.gate.take(case.id, "rev-1")


# --- The transition table ----------------------------------------------------------------------
HIGH = ClassifierResult(label="general_question", confidence=0.95)  # drafts with only customer_name
LOW = ClassifierResult(label="fee_question", confidence=0.1)
NAME_ONLY = {"customer_name": "Test Kunde"}  # card_blocked's default template drafts; fee_question lacks plan_name
FRAUD = None  # the ticket text fires the fraud rule; the model is not called


def _redraft(gate, case):
    return gate.correct_class(case.id, "rev-1", "card_blocked")


def _blocked(gate, case):
    return gate.correct_class(case.id, "rev-1", "fee_question")


S = CaseState
# Every allowed pair and the real code path that makes it: (model answer, reviewer action or None for the pipeline).
PATHS = {
    (S.NEW, S.DRAFTED): (HIGH, None),
    (S.NEW, S.NEEDS_PERSON): (LOW, None),
    (S.NEW, S.ESCALATED): (FRAUD, None),
    (S.ESCALATED, S.TAKEN): (FRAUD, lambda g, c: g.take(c.id, "rev-1")),
    (S.DRAFTED, S.SENT): (HIGH, lambda g, c: g.approve(c.id, "rev-1", c.draft_hash)),
    (S.DRAFTED, S.ESCALATED): (HIGH, lambda g, c: g.escalate(c.id, "rev-1")),
    (S.DRAFTED, S.DRAFTED): (HIGH, _redraft),
    (S.DRAFTED, S.NEEDS_PERSON): (HIGH, _blocked),
    (S.NEEDS_PERSON, S.DRAFTED): (LOW, _redraft),
    (S.NEEDS_PERSON, S.ESCALATED): (LOW, lambda g, c: g.escalate(c.id, "rev-1")),
    (S.NEEDS_PERSON, S.NEEDS_PERSON): (LOW, _blocked),
}


def gate_env(make_env, result):
    env = make_env(llm=FakeLLM(script=[result or HIGH]))
    return env, Gate(env.store, env.audit, env.source, env.config)


def test_every_allowed_pair_is_made_by_its_code_path(make_env):
    assert set(PATHS) == {(src, dst) for src, targets in ALLOWED_TRANSITIONS.items() for dst in targets}
    for (src, dst), (result, action) in PATHS.items():
        env, gate = gate_env(make_env, result)
        case = env.run(ticket("This is fraud" if result is FRAUD else "invoice?", metadata=NAME_ONLY))
        if action is not None:
            assert case.state == src, (src, dst)
            case = action(gate, case)
        assert case.state == dst and env.store.get(case.id).state == dst, (src, dst)
        assert env.audit.events(case.id)[-1].event in {"routed", "sent", "taken", "escalated"}, (src, dst)


def test_every_pair_outside_the_table_is_rejected():
    received = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    for src in CaseState:
        for dst in CaseState:
            case = Case(id="c", ticket_id="t", language="en", received_at=received, config_version="v", state=src)
            if dst in ALLOWED_TRANSITIONS.get(src, set()):
                case.transition(dst)
                assert case.state == dst
            else:
                with pytest.raises(InvalidTransition):
                    case.transition(dst)
                assert case.state == src
    assert FINAL_STATES == {S.SENT, S.TAKEN} and S.NEW in OPEN_STATES


@pytest.mark.parametrize("closed", [S.SENT, S.TAKEN])
def test_closed_cases_reject_edit_escalate_and_class_change(make_env, closed):
    env, gate = gate_env(make_env, HIGH)
    case = env.run(ticket("invoice?" if closed == S.SENT else "This is fraud", metadata=NAME_ONLY))
    case = gate.approve(case.id, "rev-1", case.draft_hash) if closed == S.SENT else gate.take(case.id, "rev-1")
    before, rows, sent = env.store.get(case.id), len(env.audit.events()), list(env.source.sent)
    actions = [
        lambda: gate.edit(case.id, "rev-2", "Hello, a later edit."),
        lambda: gate.escalate(case.id, "rev-2"),
        lambda: gate.correct_class(case.id, "rev-2", "card_blocked"),
        lambda: gate.correct_class(case.id, "rev-2", "fraud_report"),
    ]
    for action in actions:
        with pytest.raises(GateError):
            action()
    assert env.store.get(case.id) == before and before.state == closed
    assert len(env.audit.events()) == rows and env.source.sent == sent


# --- Static checks -----------------------------------------------------------------------------
def test_send_reply_only_called_in_gate_and_ticket_sources():
    allowed = {"gate.py", "ticket_source.py"}
    offenders = [p.name for p in SRC.rglob("*.py") if "send_reply(" in p.read_text(encoding="utf-8") and p.name not in allowed]
    assert offenders == []
    assert "send_reply(" in (SRC / "gate.py").read_text(encoding="utf-8")


def test_pipeline_never_imports_ticket_source():
    tree = ast.parse((SRC / "pipeline.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""} | {a.name for a in node.names}
    assert not any("ticket_source" in name or "TicketSource" in name or name == "Gate" for name in imported)
    assert "send_reply" not in (SRC / "pipeline.py").read_text(encoding="utf-8")
