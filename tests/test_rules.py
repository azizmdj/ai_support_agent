import pytest
from conftest import ticket

from triage_agent.llm import FakeLLM
from triage_agent.models import CaseState, ClassifierResult
from triage_agent.rules import match_rules, normalize

FRAUD_VARIANTS = [
    ("en", "This is FRAUD, someone used my card"),
    ("en", "i think this is fraudulent"),
    ("en", "There is a payment I did not make"),
    ("en", "Unauthorised payment on my account"),
    ("de", "Betrug! Jemand hat abgebucht"),
    ("de", "BETRUG"),
    ("de", "Das ist eine betrügerische Abbuchung"),
    ("de", "BETRÜGERISCHE Zahlung"),
    ("de", "Die Zahlung war nicht von mir"),
    ("de", "Ich habe Anzeige erstattet bei der Polizei"),
]


def test_normalize_casefolds_strips_accents_and_eszett():
    assert normalize("BETRÜG  Straße") == "betrug strasse"


@pytest.mark.parametrize("lang,text", FRAUD_VARIANTS)
def test_fraud_variants_escalate_without_calling_llm(make_env, lang, text):
    llm = FakeLLM(script=[ClassifierResult(label="general_question", confidence=0.99, reason="looks harmless")])
    env = make_env(llm=llm)
    case = env.run(ticket(text, language=lang))
    assert case.class_name == "fraud_report"
    assert case.state == CaseState.ESCALATED
    assert case.draft is None and case.template_id is None
    assert llm.calls == 0


def test_language_restricted_rule_only_fires_for_its_language(config):
    assert match_rules(ticket("Anzeige erstattet", language="de"), config).rule.id == "fraud_police_de"
    assert match_rules(ticket("Anzeige erstattet", language="en"), config) is None


def test_highest_priority_class_wins_on_conflict(config):
    hit = match_rules(ticket("Formal complaint: this was fraud and nobody helped"), config)
    assert hit.class_name == "fraud_report"


def test_keywords_match_at_word_start_only(config):
    # "defraud" does not start with "fraud"; no rule should fire.
    assert match_rules(ticket("They tried to defraud nobody, I only ask about fees"), config) is None


def test_complaint_rule_escalates(make_env):
    env = make_env(llm=FakeLLM(script=[ClassifierResult(label="fee_question", confidence=0.99)]))
    case = env.run(ticket("Ich möchte eine Beschwerde einreichen", language="de"))
    assert (case.class_name, case.state, env.llm.calls) == ("complaint", CaseState.ESCALATED, 0)


def test_route_rule_forces_class_skips_llm_and_still_drafts(pack, make_env):
    from conftest import edit_yaml

    from triage_agent.config import load_config

    edit_yaml(pack / "rules.yaml", lambda d: d["rules"].append(
        {"id": "invoice_route", "patterns": ["invoice"], "action": "route", "force_class": "general_question"}))
    env = make_env(llm=FakeLLM(script=[ClassifierResult(label="fee_question", confidence=0.99)]), cfg=load_config(pack))
    case = env.run(ticket("Where is my INVOICE?"))
    assert (case.class_name, case.rule_id, case.state, env.llm.calls) == (
        "general_question", "invoice_route", CaseState.DRAFTED, 0)
