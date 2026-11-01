from triage_agent.__main__ import main
from triage_agent.models import CaseState


def test_fixture_tickets_take_the_expected_paths(make_env, fixture_tickets):
    env = make_env()
    got = {t.id: (c.class_name, c.state) for t in fixture_tickets.values() for c in [env.run(t)]}
    E, D, N = CaseState.ESCALATED, CaseState.DRAFTED, CaseState.NEEDS_PERSON
    assert got == {
        "T01": ("fee_question", D),
        "T02": ("fee_question", D),
        "T03": ("fraud_report", E),
        "T04": ("fraud_report", E),
        "T05": ("fraud_report", E),
        "T06": ("complaint", E),
        "T07": ("complaint", E),  # no rule; model says complaint, locked class escalates
        "T08": ("card_blocked", D),
        "T09": ("card_blocked", N),  # missing card_last4
        "T10": ("card_blocked", D),  # German card template
        "T11": ("pending_transfer", D),
        "T12": ("unclear", N),  # vague
        "T13": ("unclear", N),  # two issues -> low confidence
        "T14": ("general_question", D),  # injection attempt, drafted normally
        "T15": ("pending_transfer", D),
    }


def test_demo_runs(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "fraud_report" in out and "reviewer 'demo' approved" in out
