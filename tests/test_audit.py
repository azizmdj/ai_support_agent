import pytest
from conftest import ticket

from triage_agent.config import load_config
from triage_agent.llm import FakeLLM
from triage_agent.models import CaseState, ClassifierResult


def test_every_step_writes_a_row_with_config_version(make_env, config):
    env = make_env(llm=FakeLLM(script=[ClassifierResult(label="general_question", confidence=0.9)]))
    case = env.run(ticket("How do I get my invoice?"))
    env.gate.edit(case.id, "rev-1", case.draft + "\nP.S. edited")
    env.gate.approve(case.id, "rev-1", env.store.get(case.id).draft_hash)
    events = env.audit.events(case.id)
    assert [e.event for e in events] == [
        "received", "classified", "template_selected", "drafted", "routed", "edited", "approved", "sent",
    ]
    assert {e.config_version for e in events} == {config.version}
    assert all(e.case_id == case.id and e.ts for e in events)


def test_rule_and_take_steps_are_logged(make_env, config):
    env = make_env()
    case = env.run(ticket("BETRUG", language="de"))
    env.gate.take(case.id, "rev-1")
    events = env.audit.events(case.id)
    assert [e.event for e in events] == ["received", "rule_matched", "routed", "taken"]
    assert events[1].payload == {"rule_id": "fraud_keywords", "class": "fraud_report"}
    assert {e.config_version for e in events} == {config.version}


def test_payloads_never_contain_message_text(make_env, fixture_tickets):
    env = make_env()
    for t in fixture_tickets.values():
        case = env.run(t)
        if case.state == CaseState.DRAFTED:
            env.gate.approve(case.id, "rev-1", case.draft_hash)
    rows = env.conn.execute("SELECT payload_json FROM audit").fetchall()
    dump = "\n".join(r[0] for r in rows).casefold()
    for t in fixture_tickets.values():
        assert t.text.casefold() not in dump
        for word in ("ignore your instructions", "betrug", "unhappy", "refund", "supplier"):
            assert word not in dump


def test_audit_rejects_free_text_payload(make_env):
    env = make_env()
    with pytest.raises(ValueError):
        env.audit.write("c1", "pipeline", "classified", message="customer text")
    with pytest.raises(ValueError):
        env.audit.write("c1", "pipeline", "classified", reason_code="x" * 200)


def test_editing_a_template_changes_config_version(pack, config):
    assert load_config(pack).version == config.version
    path = pack / "templates/en/fee_question_reply.md"
    path.write_text(path.read_text(encoding="utf-8").replace("thank you", "many thanks"), encoding="utf-8")
    new = load_config(pack)
    assert new.version != config.version


def test_new_rows_carry_the_new_version(pack, make_env, config):
    path = pack / "rules.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    new_config = load_config(pack)
    env = make_env(cfg=new_config)
    case = env.run(ticket("fraud"))
    assert case.config_version == new_config.version != config.version
    assert {e.config_version for e in env.audit.events(case.id)} == {new_config.version}
