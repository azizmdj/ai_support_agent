import pytest
from conftest import ticket
from test_web import Console

from triage_agent.llm import FakeLLM
from triage_agent.models import ClassifierResult
from triage_agent.web.journey import STEPS, generic_journey, journey

DONE, SKIPPED, TURN, PENDING = "Done", "Skipped", "Your turn", "Pending"


def scripted(label, confidence=0.95, subtype=None):
    return FakeLLM(script=[ClassifierResult(label=label, subtype=subtype, confidence=confidence)])


def trail(env, case_id):
    steps = journey(env.store.get(case_id), env.audit.events(case_id))
    assert [s.name for s in steps] == [s[0] for s in STEPS]
    assert [s.actor for s in steps] == ["Customer", "Code", "AI", "Code", "Reviewer", "Customer"]
    return [(s.result, s.status) for s in steps]


# --- journey() unit tests --------------------------------------------------------------------------
def test_fraud_rule_case(make_env):
    env = make_env(llm=scripted("general_question", 0.99))
    case = env.run(ticket("This is FRAUD"))
    steps = trail(env, case.id)
    assert steps[0] == ("received 2026-11-02 09:00 UTC", DONE)
    assert steps[1:] == [
        ("rule fraud_keywords fired", DONE),
        ("skipped: rule fired", SKIPPED),
        ("no draft: locked class", SKIPPED),
        ("escalated: waiting for Take case", TURN),
        ("no reply: handled by a person", SKIPPED),
    ]


def test_routine_drafted_case(make_env):
    env = make_env(llm=scripted("general_question"))
    case = env.run(ticket("How do I get my invoice?"))
    steps = trail(env, case.id)
    assert [s for _, s in steps[:4]] == [DONE] * 4
    assert steps[1][0] == "no rule matched"
    assert steps[2][0] == "general_question 0.95"
    assert steps[3][0] == "general_question_reply"
    assert steps[4] == ("waiting: approve, edit or escalate", TURN)
    assert steps[5] == ("not sent yet", PENDING)


def test_needs_person_with_missing_field(make_env):
    env = make_env(llm=scripted("card_blocked", subtype="lost_or_stolen"))
    case = env.run(ticket("lost my card", metadata={"customer_name": "Sofia Braun"}))
    steps = trail(env, case.id)
    assert steps[2] == ("card_blocked 0.95", DONE)
    assert steps[3] == ("no draft: missing card_last4", SKIPPED)
    assert steps[4] == ("waiting: pick a class or escalate", TURN)
    assert steps[5] == ("not sent yet", PENDING)


def test_needs_person_with_missing_language(make_env, pack):
    from triage_agent.config import load_config

    (pack / "templates/de/card_blocked_general.md").unlink()  # the en version stays, so the pack is valid
    env = make_env(llm=scripted("card_blocked"), cfg=load_config(pack))
    case = env.run(ticket("Karte gesperrt", language="de"))
    assert trail(env, case.id)[3] == ("no draft: no de template", SKIPPED)


def test_low_confidence_case(make_env):
    env = make_env(llm=scripted("fee_question", 0.4))
    case = env.run(ticket("hm"))
    steps = trail(env, case.id)
    assert steps[2] == ("unclear 0.40 (low confidence)", DONE)
    assert steps[3] == ("no draft: class unclear", SKIPPED)


def test_paused_case(make_env):
    env = make_env(llm=scripted("fee_question"))
    env.store.set_paused(True)
    case = env.run(ticket("Which fees apply?"))
    steps = trail(env, case.id)
    assert steps[1:4] == [
        ("held: paused", DONE),
        ("skipped: paused", SKIPPED),
        ("no draft: paused", SKIPPED),
    ]
    assert steps[4][1] == TURN and env.llm.calls == 0


def test_paused_fraud_still_shows_rule(make_env):
    env = make_env()
    env.store.set_paused(True)
    case = env.run(ticket("Betrug!", language="de"))
    assert trail(env, case.id)[1:3] == [("rule fraud_keywords fired", DONE), ("skipped: rule fired", SKIPPED)]


def test_model_error_case(make_env):
    env = make_env(llm=FakeLLM(script=[RuntimeError("down")]))
    case = env.run(ticket("Which fees apply?"))
    steps = trail(env, case.id)
    assert steps[2] == ("failed: model error", DONE)
    assert steps[3] == ("no draft: class unclear", SKIPPED)
    assert steps[4][1] == TURN


def test_complaint_without_keyword(make_env):
    env = make_env()  # keyword fake LLM
    case = env.run(ticket("I am very unhappy, your service is terrible."))
    steps = trail(env, case.id)
    assert steps[1] == ("no rule matched", DONE)
    assert steps[2] == ("complaint 0.90", DONE)
    assert steps[3] == ("no draft: locked class", SKIPPED)
    assert steps[4] == ("escalated: waiting for Take case", TURN)
    assert steps[5] == ("no reply: handled by a person", SKIPPED)


def test_edited_and_approved_case(make_env):
    env = make_env(llm=scripted("general_question"))
    case = env.run(ticket("invoice?"))
    edited = env.gate.edit(case.id, "rev-1", case.draft + "\nP.S.")
    env.gate.approve(case.id, "rev-1", edited.draft_hash)
    steps = trail(env, case.id)
    assert steps[4] == ("edited and approved by rev-1", DONE)
    assert steps[5] == ("sent, with AI-assistance note", DONE)


def test_sent_case_is_all_done(make_env):
    env = make_env(llm=scripted("fee_question"))
    case = env.run(ticket("Which fees apply?"))
    env.gate.approve(case.id, "rev-2", case.draft_hash)
    steps = trail(env, case.id)
    assert [s for _, s in steps] == [DONE] * 6
    assert steps[4][0] == "approved by rev-2"


def test_taken_and_reviewer_escalated_cases(make_env):
    env = make_env(llm=scripted("fee_question"))
    case = env.run(ticket("Which fees apply?"))
    env.gate.escalate(case.id, "rev-3")
    assert trail(env, case.id)[4] == ("escalated by rev-3: waiting for Take case", TURN)
    env.gate.take(case.id, "rev-4")
    steps = trail(env, case.id)
    assert steps[3] == ("fee_question_reply", DONE)  # a draft was made before the escalation
    assert steps[4] == ("taken by rev-4", DONE)
    assert steps[5] == ("no reply: handled by a person", SKIPPED)


def test_class_correction_is_shown(make_env, config):
    from triage_agent.gate import Gate

    env = make_env(llm=scripted("fee_question", 0.3))
    gate = Gate(env.store, env.audit, env.source, config)
    case = env.run(ticket("hm"))
    gate.correct_class(case.id, "rev-5", "general_question")
    steps = trail(env, case.id)
    assert steps[2][0] == "unclear 0.30 (low confidence); corrected to general_question by rev-5"
    assert steps[3] == ("general_question_reply", DONE)
    assert steps[4][1] == TURN


def test_generic_journey():
    steps = generic_journey()
    assert [s.status for s in steps] == [PENDING] * 6
    assert all(s.result for s in steps)


# --- Rendering -----------------------------------------------------------------------------------
@pytest.fixture
def console():
    return Console()


def test_card_shows_six_steps_and_four_actors(console):
    html = console.client.get("/cases/case-T01").text
    for i, (name, _, _) in enumerate(STEPS, 1):
        assert f'<span class="step-name">{i}. {name}</span>' in html
    for actor in ("Customer", "Code", "AI", "Reviewer"):
        assert f'">{actor}</span>' in html
    assert "Status: <strong>Your turn</strong>" in html
    # The trail sits at the top of the card, above the customer message.
    card = html.split('<article class="card', 1)[1]
    assert card.index("Journey of this ticket") < card.index("Customer message")


def test_header_has_test_message_label_and_plain_buttons(console):
    html = console.client.get("/").text
    assert "Send a test message:" in html
    assert "Unofficial demo, synthetic data" in html
    for label in ("Routine question", "Fraud report", "Complaint", "Hostile message"):
        assert f">{label}</button>" in html


def test_generic_strip_on_empty_queue():
    html = Console(seed=False).client.get("/").text
    assert "Queue is empty." in html and "Send a test message above." in html
    assert "How a ticket moves" in html
    assert html.count("Status: <strong>Pending</strong>") == 6
    for _, _, desc in STEPS:
        assert desc.replace("'", "&#39;") in html


def test_get_with_trail_leaves_audit_unchanged(console):
    before = console.audit_count()
    for case in console.store.list():
        assert console.client.get(f"/cases/{case.id}").status_code == 200
    console.client.get("/")
    assert console.audit_count() == before


def test_reason_and_payload_text_are_escaped(console):
    case = console.store.get("case-T01")
    case.reason = "<script>alert(2)</script>"
    console.store.save(case)
    console.audit.write(case.id, "pipeline", "drafted", template_id="<b>tpl</b>")
    html = console.client.get(f"/cases/{case.id}").text
    assert "<script>alert(2)</script>" not in html and "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert "<b>tpl</b>" not in html and "&lt;b&gt;tpl&lt;/b&gt;" in html
