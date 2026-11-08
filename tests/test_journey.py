from conftest import ticket

from triage_agent.llm import FakeLLM
from triage_agent.models import ClassifierResult
from triage_agent.web.journey import STEPS, journey

DONE, SKIPPED, TURN, PENDING = "Done", "Skipped", "Your turn", "Pending"


def scripted(label, confidence=0.95, subtype=None):
    return FakeLLM(script=[ClassifierResult(label=label, subtype=subtype, confidence=confidence)])


def trail(env, case_id):
    steps = journey(env.store.get(case_id), env.audit.events(case_id))
    assert [s.name for s in steps] == [name for name, *_ in STEPS]
    assert [s.actor for s in steps] == ["Customer", "Code", "AI", "Code", "Reviewer", "Code"]
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
        ("no draft: rule fraud_keywords fired", SKIPPED),
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
    assert steps[2] == ("card_blocked 0.95; subtype lost_or_stolen", DONE)
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


def test_steps_carry_input_and_config(make_env):
    from triage_agent.llm import FallbackChain
    from triage_agent.web.journey import Context

    ctx = Context(threshold=0.7, rule_count=3, providers={"fake": "fake: keyword fake (offline)"},
                  chain="fake", masked="Fees? IBAN <IBAN_1>", reviewer="rev-1")
    env = make_env(llm=FallbackChain([("fake", FakeLLM(script=[ClassifierResult(label="fee_question", confidence=0.9)]))]))
    case = env.run(ticket("Which fees apply?"))
    steps = {s.name: s for s in journey(env.store.get(case.id), env.audit.events(case.id), ctx)}
    assert steps["Rules"].config == "rules.yaml (3 rules)"
    assert steps["Classify"].input == "the masked message: Fees? IBAN <IBAN_1>"
    assert steps["Classify"].result == "fee_question 0.90 via fake"
    assert steps["Classify"].config == "fake: keyword fake (offline), threshold 0.7"
    assert steps["Template"].input == "class fee_question, language en, ticket fields"
    assert steps["Review"].input.startswith("draft ") and steps["Review"].config == "approval gate, reviewer rev-1"
    assert steps["Reply"].config == "AI-disclosure line (en) from settings.yaml"

    fraud = env.run(ticket("This is fraud", id="F1"))
    steps = {s.name: s for s in journey(env.store.get(fraud.id), env.audit.events(fraud.id), ctx)}
    assert (steps["Classify"].input, steps["Classify"].status) == ("nothing: no model call", SKIPPED)
    assert steps["Reply"].input == "-" and steps["Review"].input == "the case, without a draft"
