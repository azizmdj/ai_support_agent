import pytest
from conftest import ticket

from triage_agent import masking
from triage_agent.llm import FallbackChain, LLMClassifier
from triage_agent.masking import mask, unmask
from triage_agent.models import CaseState


class CountingModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return '{"label": "fee_question", "confidence": 0.9}'


@pytest.mark.parametrize("value,kind", [
    ("DE89 3704 0044 0532 0130 00", "IBAN"),
    ("DE89370400440532013000", "IBAN"),
    ("de89 3704 0044 0532 0130 00", "IBAN"),
    ("GB29NWBK60161331926819", "IBAN"),
    ("4111 1111 1111 1111", "CARD"),
    ("5500-0000-0000-0004", "CARD"),
    ("4111111111111", "CARD"),  # 13 digits
    ("lena.hartmann+bank@example.org", "EMAIL"),
    ("+49 151 23456789", "PHONE"),
    ("0151/2345678", "PHONE"),
    ("030 1234567", "PHONE"),
])
def test_each_pattern(value, kind):
    masked, mapping = mask(f"Hello, here it is: {value}. Thanks")
    assert value not in masked
    assert f"<{kind}_1>" in masked
    assert mapping == {f"<{kind}_1>": value}


def test_mistyped_iban_is_still_masked():
    bad = "DE00 3704 0044 0532 0130 99"  # wrong checksum
    masked, _ = mask(f"IBAN {bad}")
    assert bad not in masked and "<IBAN_1>" in masked


def test_numbering_and_repeats():
    text = ("Mail a@x.de or b@y.de, again a@x.de. "
            "Cards 4111 1111 1111 1111 and 5500 0000 0000 0004. Call +49 30 1234567.")
    masked, mapping = mask(text)
    assert masked == ("Mail <EMAIL_1> or <EMAIL_2>, again <EMAIL_1>. "
                      "Cards <CARD_1> and <CARD_2>. Call <PHONE_1>.")
    assert mapping["<EMAIL_2>"] == "b@y.de"
    assert len(mapping) == 5


def test_round_trip():
    text = "IBAN DE89 3704 0044 0532 0130 00, card 4111-1111-1111-1111, x@y.org, +49 151 23456789"
    masked, mapping = mask(text)
    assert all(v not in masked for v in mapping.values())
    assert unmask(masked, mapping) == text


def test_ordinary_text_is_untouched():
    text = "My transfer TRX-2026-0042 of 300 EUR on 2026-11-02 is pending since Friday."
    assert mask(text) == (text, {})


def test_names_are_not_detected():
    # Documented limit: names and free-text addresses pass through.
    masked, _ = mask("I am Lena Hartmann, Hauptstraße 5, Dresden")
    assert "Lena Hartmann" in masked


def test_mask_failure_fails_closed(make_env, monkeypatch):
    model = CountingModel()
    fallback = CountingModel()
    chain = FallbackChain([("real", LLMClassifier(model)), ("second", LLMClassifier(fallback))])

    def broken(text):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(masking, "mask", broken)
    env = make_env(llm=chain)
    case = env.run(ticket("Which fees apply? IBAN DE89 3704 0044 0532 0130 00"))
    assert model.calls == 0 and fallback.calls == 0
    assert case.state == CaseState.NEEDS_PERSON
    rows = [e for e in env.audit.events(case.id) if e.event == "mask_failed"]
    assert rows and rows[0].payload == {"error_type": "MaskingError", "reason_code": "mask_failed"}
    assert "model_error" not in env.events(case.id)
