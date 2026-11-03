import pytest
from conftest import ticket

from triage_agent.llm import FakeLLM
from triage_agent.models import CaseState, ClassifierResult
from triage_agent.rules import match_rules, normalize

RULE_VARIANTS = [
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
RULE_VARIANTS = [(lang, text, "fraud_report") for lang, text in RULE_VARIANTS] + [
    ("de", "Ich möchte eine Beschwerde einreichen", "complaint"),
    ("en", "This is a formal complaint", "complaint"),
]


def test_normalize_casefolds_strips_accents_and_eszett():
    assert normalize("BETRÜG  Straße") == "betrug strasse"


@pytest.mark.parametrize("lang,text,expected", RULE_VARIANTS)
def test_rule_variants_escalate_without_calling_llm(make_env, lang, text, expected):
    llm = FakeLLM(script=[ClassifierResult(label="general_question", confidence=0.99, reason="looks harmless")])
    env = make_env(llm=llm)
    case = env.run(ticket(text, language=lang))
    assert case.class_name == expected
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
