import json
import re
from collections import Counter
from datetime import date

import pytest

from triage_agent import DEFAULT_PACK
from triage_agent import evaluation as ev
from triage_agent.config import ConfigError, load_config
from triage_agent.evaluation import TicketResult, compute_metrics, leaked
from triage_agent.llm import LLMClassifier

TICKETS = ev.load_eval_tickets()
MIN_COUNTS = {"fraud_report": 14, "complaint": 12, "card_blocked": 16, "pending_transfer": 14,
              "fee_question": 14, "general_question": 10, "unclear": 12}


@pytest.fixture(scope="module")
def fake_runs():
    config = load_config(DEFAULT_PACK)
    return {split: ev.evaluate(config, split, provider_ids="fake", env={}) for split in ("dev", "test")}


# --- Ticket file -----------------------------------------------------------------------------------
def test_ticket_file_schema_and_ids():
    assert 90 <= len(TICKETS) <= 110
    assert len({t.id for t in TICKETS}) == len(TICKETS)
    config = load_config(DEFAULT_PACK)
    for t in TICKETS:
        assert t.true_class in config.classes, t.id
        if t.true_subtype:
            assert t.true_subtype in config.classes[t.true_class].subtypes, t.id


def test_seeded_pii_and_canaries_occur_in_messages():
    for t in TICKETS:
        for value in t.pii:
            assert value in t.message, (t.id, "pii")
        if "injection" in t.tags:
            assert t.canary and t.canary in t.message, t.id
    canaries = [t.canary for t in TICKETS if t.canary]
    assert len(canaries) == len(set(canaries)) >= 8
    with_pii = sum(bool(t.pii) for t in TICKETS)
    assert 0.4 <= with_pii / len(TICKETS) <= 0.6


def test_class_counts_and_splits():
    injections = [t for t in TICKETS if "injection" in t.tags]
    plain = Counter(t.true_class for t in TICKETS if "injection" not in t.tags)
    for cls, minimum in MIN_COUNTS.items():
        assert plain[cls] >= minimum, cls
    assert len(injections) >= 8 and len({t.true_class for t in injections}) >= 5
    dev = sum(t.split == "dev" for t in TICKETS) / len(TICKETS)
    assert 0.55 <= dev <= 0.65
    for cls in MIN_COUNTS:  # stratified: every class in both splits
        assert {t.split for t in TICKETS if t.true_class == cls} == {"dev", "test"}


def test_hard_cases_are_present():
    tags = Counter(tag for t in TICKETS for tag in t.tags)
    for tag in ("two_issues", "mixed_language", "rude", "other_language", "typo", "missing_field",
                "mistyped_iban", "tax", "legal", "short", "vague"):
        assert tags[tag] >= 1, tag
    no_kw = Counter(t.true_class for t in TICKETS if "no_keyword" in t.tags)
    assert no_kw["fraud_report"] >= 5 and no_kw["complaint"] >= 4
    assert {t.language for t in TICKETS} >= {"en", "de", "fr", "tr"}
    subtypes = {t.true_subtype for t in TICKETS if t.true_class == "card_blocked"}
    assert subtypes == {None, "lost_or_stolen", "blocked_after_pin_attempts"}


def test_risky_keyword_free_tickets_really_have_no_rule_keyword():
    from triage_agent.rules import match_rules

    config = load_config(DEFAULT_PACK)
    for t in TICKETS:
        if "no_keyword" in t.tags:
            assert match_rules(t.to_ticket(), config) is None, t.id


def test_expected_templates_exist():
    config = load_config(DEFAULT_PACK)
    for t in TICKETS:
        if t.expected_template:
            assert (t.expected_template, t.language) in config.templates, t.id
        if t.language not in ("en", "de") and t.true_class != "unclear":
            assert t.expected_state == "needs_person", t.id


def test_every_drafting_class_has_en_and_de_templates():
    config = load_config(DEFAULT_PACK)
    ids = {ref.id for c in config.classes.values() for ref in c.templates}
    assert len(ids) == 6 and len(config.templates) == 12
    for tid in ids:
        assert (tid, "en") in config.templates and (tid, "de") in config.templates


# --- Metric functions ------------------------------------------------------------------------------
def result(**kw):
    base = dict(id="X", split="dev", language="en", true_class="fee_question", true_subtype=None,
                expected_state="drafted", expected_template="fee_question_reply", pred_class="fee_question",
                pred_subtype=None, state="drafted", template_id="fee_question_reply", provider="fake",
                risky=False, has_pii=False, reached_model=True, pii_leaked=False, injection=False,
                injection_pass=None)
    base.update(kw)
    return TicketResult(**base)


def test_metrics_on_hand_made_results():
    rows = [
        result(id="a"),                                                       # correct draft
        result(id="b", pred_class="general_question", template_id="general_question_reply"),  # wrong draft
        result(id="c", true_class="fraud_report", risky=True, expected_state="escalated", expected_template=None,
               pred_class="fraud_report", state="escalated", template_id=None),
        result(id="d", true_class="complaint", risky=True, expected_state="escalated", expected_template=None,
               pred_class="general_question", state="drafted", template_id="general_question_reply"),
        result(id="e", true_class="unclear", expected_state="needs_person", expected_template=None,
               pred_class="unclear", state="needs_person", template_id=None, has_pii=True, pii_leaked=True),
        result(id="f", has_pii=True, injection=True, injection_pass=True),
    ]
    m = compute_metrics(rows)
    assert m["class_accuracy"] == 4 / 6
    assert m["risky_escalated_rate"] == 0.5 and m["risky_drafted"] == 1 and m["risky_class_recall"] == 0.5
    assert m["wrong_draft_rate"] == 2 / 4          # b and d among drafted a, b, d, f
    assert m["coverage"] == 1.0                    # a, b, f are in scope and drafted
    assert m["template_accuracy"] == 2 / 3         # a, f right; b wrong
    assert m["pii_leak_rate"] == 0.5 and m["injection_pass_rate"] == 1.0
    assert m["per_class"]["fee_question"] == {"recall": 2 / 3, "precision": 1.0, "support": 3}
    assert m["per_class"]["general_question"]["precision"] == 0.0
    assert m["per_class"]["unclear"]["recall"] == 1.0


def test_wrong_subtype_is_a_wrong_draft():
    row = result(true_class="card_blocked", true_subtype="lost_or_stolen", pred_class="card_blocked",
                 pred_subtype=None)
    assert ev.is_wrong_draft(row)


def test_leak_detection_handles_separators():
    assert leaked(["DE89 3704 0044 0532 0130 00"], ["iban DE89370400440532013000"])
    assert leaked(["4111-1111-1111-1111"], ["card 4111 1111 1111 1111"])
    assert not leaked(["DE89 3704 0044 0532 0130 00"], ["iban <IBAN_1>"])


def test_regressions_compare_both_directions():
    base = {"class_accuracy": 0.6, "pii_leak_rate": 0.0}
    assert ev.regressions({"class_accuracy": 0.61, "pii_leak_rate": 0.0}, base) == []
    assert len(ev.regressions({"class_accuracy": 0.5, "pii_leak_rate": 0.1}, base)) == 2


# --- Recording wrapper -----------------------------------------------------------------------------
def test_recorder_never_sees_seeded_values():
    config = load_config(DEFAULT_PACK)
    harness = ev.build_harness(config, "fake", env={})
    harness.current[0] = "T1"
    seeded = ["DE89 3704 0044 0532 0130 00", "4111 1111 1111 1111", "x.y@example.com", "+49 151 5550 1234"]
    harness.chain.classify("Fees? " + ", ".join(seeded), [c for c in config.classes.values() if c.name != "unclear"])
    payloads = harness.payloads["T1"]
    assert payloads and "<IBAN_1>" in payloads[1]
    assert not leaked(seeded, payloads)


def test_recorder_wraps_real_adapters(monkeypatch):
    import httpx

    config = load_config(DEFAULT_PACK)

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"label": "fee_question", "confidence": 0.9}'}}]})

    harness = ev.build_harness(config, "openai_main", env={"OPENAI_API_KEY": "test-key-not-real-0005"},
                               transport=httpx.MockTransport(handler))
    provider = harness.chain.providers[0][1]
    assert isinstance(provider, LLMClassifier) and isinstance(provider.model, ev.RecordingTextModel)
    assert harness.models == {"openai_main": "gpt-4o-mini"}


def test_live_run_without_key_fails_clearly():
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        ev.build_harness(load_config(DEFAULT_PACK), "openai_main", env={})


# --- Regression against the baseline and hard invariants -------------------------------------------
def test_fake_run_does_not_drop_below_baseline(fake_runs):
    baseline = json.loads(ev.BASELINE_PATH.read_text(encoding="utf-8"))
    for split, run in fake_runs.items():
        assert ev.regressions(run.metrics, baseline[split]) == [], split


@pytest.mark.parametrize("split", ["dev", "test"])
def test_invariants_with_fake_setup(fake_runs, split):
    m = fake_runs[split].metrics
    assert m["pii_leak_rate"] == 0
    assert m["pii_tickets_reaching_model"] > 0  # masking was really exercised
    assert m["replies_sent"] == 0
    assert all(r.state != "sent" for r in fake_runs[split].results)
    assert m["injection_pass_rate"] == 1.0


@pytest.mark.xfail(strict=True, reason="Target 0. The keyword fake drafts two keyword-free fraud reports in dev "
                                       "(FR01 -> general_question, FR11 -> card_blocked). Not tuned on purpose; "
                                       "when this passes, remove the xfail and lower the baseline.")
def test_no_risky_ticket_drafted_target(fake_runs):
    assert sum(run.metrics["risky_drafted"] for run in fake_runs.values()) == 0


def test_fake_run_is_deterministic(fake_runs):
    again = ev.evaluate(load_config(DEFAULT_PACK), "test", provider_ids="fake", env={})
    assert again.metrics == fake_runs["test"].metrics


def test_sweep_only_on_dev(fake_runs):
    assert [r["threshold"] for r in fake_runs["dev"].sweep] == [0.5, 0.6, 0.7, 0.8, 0.9]
    assert fake_runs["test"].sweep is None


# --- Report ----------------------------------------------------------------------------------------
def test_report_has_no_message_text_pii_or_keys(fake_runs, tmp_path):
    path = ev.write_report(fake_runs["dev"], tmp_path, today=date(2026, 11, 2))
    assert path.name == "fake-dev-2026-11-02.md"
    text = path.read_text(encoding="utf-8")
    for heading in ("config_version", "Confidence threshold", "Threshold sweep", "Misses", "pii_leak_rate",
                    "Model for fake"):
        assert heading in text
    for t in TICKETS:
        assert t.message not in text
        assert not leaked(t.pii, [text])
        if t.canary:
            assert t.canary not in text
    assert not re.search(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}", text)


def test_cli_eval_writes_report(tmp_path, capsys):
    from triage_agent.__main__ import main

    assert main(["eval", "--split", "test", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "RISKY TICKETS DRAFTED" in out and "threshold sweep" not in out
    assert len(list(tmp_path.glob("fake-test-*.md"))) == 1


def test_cli_eval_live_without_key_exits_clearly(tmp_path, capsys):
    from triage_agent.__main__ import main

    assert main(["eval", "--provider", "openai_main", "--out", str(tmp_path)]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
