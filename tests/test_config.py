import socket

import pytest
from conftest import edit_yaml

from triage_agent.config import ConfigError, load_config, parse_duration


def classes_of(data):
    return {c["name"]: c for c in data["classes"]}


def assert_rejected(pack, needle):
    with pytest.raises(ConfigError) as info:
        load_config(pack)
    assert any(needle in e for e in info.value.errors), info.value.errors


def test_shipped_pack_is_valid(config):
    assert {"fraud_report", "complaint", "card_blocked", "pending_transfer", "fee_question",
            "general_question", "unclear"} <= set(config.classes)
    assert config.classes["unclear"].team == "human_queue"
    assert config.classes["unclear"].draft_mode == "none"
    assert len(config.version) == 12


def test_network_is_blocked():
    with pytest.raises(OSError):
        socket.create_connection(("example.com", 80))
    with pytest.raises(OSError):
        socket.socket()


def test_locked_class_with_drafting_is_rejected(pack):
    edit_yaml(pack / "taxonomy.yaml", lambda d: classes_of(d)["fraud_report"].update(draft_mode="template"))
    assert_rejected(pack, "fraud_report is locked")


def test_renamed_locked_class_is_still_rejected(pack):
    """Locked is decided by the risk flag, not by the class name."""
    def change(d):
        cls = classes_of(d)["fraud_report"]
        cls.update(name="fraud_v2", draft_mode="template", templates=[{"id": "general_question_reply", "default": True}])

    edit_yaml(pack / "taxonomy.yaml", change)
    edit_yaml(pack / "rules.yaml", lambda d: [r.update(force_class="fraud_v2") for r in d["rules"]
                                              if r["force_class"] == "fraud_report"])
    assert_rejected(pack, "fraud_v2 is locked")


def test_rule_on_locked_class_must_escalate(pack):
    edit_yaml(pack / "rules.yaml", lambda d: d["rules"][0].update(action="route"))
    assert_rejected(pack, "only allows action 'escalate'")


def test_rule_with_unknown_class_is_rejected(pack):
    edit_yaml(pack / "rules.yaml", lambda d: d["rules"][0].update(force_class="no_such_class"))
    assert_rejected(pack, "unknown class 'no_such_class'")


def test_unknown_template_id_is_rejected(pack):
    text = (pack / "templates/en/general_question_reply.md").read_text(encoding="utf-8")
    (pack / "templates/en/orphan.md").write_text(text.replace("id: general_question_reply", "id: orphan"), encoding="utf-8")
    assert_rejected(pack, "unknown template id 'orphan'")


def test_missing_template_file_is_rejected(pack):
    for lang in ("en", "de"):
        (pack / f"templates/{lang}/card_blocked_pin.md").unlink()
    assert_rejected(pack, "missing template file for 'card_blocked_pin'")


def test_duplicate_class_names_are_rejected(pack):
    edit_yaml(pack / "taxonomy.yaml", lambda d: d["classes"].append(dict(classes_of(d)["complaint"])))
    assert_rejected(pack, "duplicate class name 'complaint'")


def test_declaring_builtin_unclear_is_rejected(pack):
    def change(d):
        extra = dict(classes_of(d)["complaint"], name="unclear", risk="normal")
        d["classes"].append(extra)
    edit_yaml(pack / "taxonomy.yaml", change)
    assert_rejected(pack, "duplicate class name 'unclear'")


@pytest.mark.parametrize("bad", ["24h", "P1M", "P", "PT", "P1Y", "PT-5H", ""])
def test_bad_duration_is_rejected(pack, bad):
    edit_yaml(pack / "taxonomy.yaml", lambda d: classes_of(d)["fee_question"].update(deadline=bad))
    assert_rejected(pack, "fee_question")


def test_parse_duration():
    assert parse_duration("PT1H").total_seconds() == 3600
    assert parse_duration("P2DT3H").total_seconds() == (2 * 24 + 3) * 3600


def test_template_placeholder_must_be_allowed_field(pack):
    path = pack / "templates/en/general_question_reply.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("[customer_name]", "[customer_name, iban]"), encoding="utf-8")
    assert_rejected(pack, "required_fields not in allowed_fields")


def test_crlf_does_not_change_config_version(pack, config):
    path = pack / "templates/en/fee_question_reply.md"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert load_config(pack).version == config.version
