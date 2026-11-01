import re
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import ticket
from fastapi.testclient import TestClient

import triage_agent
from triage_agent.audit import AuditLog
from triage_agent.llm import FakeLLM
from triage_agent.models import CaseState
from triage_agent.pipeline import Pipeline
from triage_agent.store import SQLiteCaseStore, connect
from triage_agent.web.app import create_app, relative_due

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
REVIEWER = "rev-test"


class Console:
    def __init__(self, seed=True, db_path=":memory:"):
        self.llm = FakeLLM()
        kwargs = {} if seed else {"seed_tickets": None}
        self.app = create_app(db_path=db_path, reviewer_id=REVIEWER, llm=self.llm, now=lambda: NOW, **kwargs)
        self.client = TestClient(self.app, follow_redirects=False)
        self.store, self.audit = self.app.state.store, self.app.state.audit
        self.sent = self.app.state.source.sent

    def post(self, url, **data):
        resp = self.client.post(url, data=data)
        assert resp.status_code == 303, resp.text
        return resp.headers["location"]

    def scenario(self, name):
        location = self.post(f"/scenarios/{name}")
        case_id = re.match(r"/cases/([^?]+)\?msg=scenario$", location).group(1)
        return self.store.get(case_id)

    def case(self, ticket_id):
        return next(c for c in self.store.list() if c.ticket_id == ticket_id)

    def events(self, case_id):
        return [e.event for e in self.audit.events(case_id)]

    def audit_count(self):
        return self.app.state.store.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]


@pytest.fixture
def console():
    return Console()


@pytest.fixture
def empty_console():
    return Console(seed=False)


def lane_of(html, ticket_id):
    for key in ("urgent", "ready", "needs"):
        section = html.split(f'class="lane lane-{key}"', 1)[1].split("</section>", 1)[0]
        if f">{ticket_id}<" in section:
            return key
    return None


# --- Network guard -------------------------------------------------------------------------------
def test_loopback_socketpair_works_but_outbound_fails():
    a, b = socket.socketpair()
    a.sendall(b"ok")
    assert b.recv(2) == b"ok"
    a.close(), b.close()
    with pytest.raises(OSError):
        socket.create_connection(("93.184.216.34", 80))
    with pytest.raises(OSError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


# --- Seeding and pages -----------------------------------------------------------------------------
def test_seed_shifts_fixtures_so_newest_is_now(console):
    cases = console.store.list()
    assert len(cases) == 15
    assert max(c.received_at for c in cases) == NOW


def test_page_renders_lanes_and_header(console):
    html = console.client.get("/").text
    assert "Unofficial demo, synthetic data" in html
    assert REVIEWER in html
    assert "Pause: <strong>OFF</strong>" in html
    for title in ("Urgent", "Ready to approve", "Needs a person"):
        assert title in html
    assert "Urgent <span class=\"count\">(5)</span>" in html
    assert "Ready to approve <span class=\"count\">(7)</span>" in html
    assert "Needs a person <span class=\"count\">(3)</span>" in html
    # Default selection is the most urgent case: fraud has the shortest deadline.
    assert 'aria-current="true"' in html
    assert "https://" not in html and "<script" not in html  # no CDN, no JavaScript


def test_get_requests_never_change_state(console):
    before = console.audit_count()
    pages = ["/", "/?msg=sent", "/static/style.css"] + [f"/cases/{c.id}" for c in console.store.list()]
    for url in pages:
        assert console.client.get(url).status_code == 200, url
    for action in ("approve", "escalate", "take", "class"):
        assert console.client.get(f"/cases/case-T01/{action}").status_code == 405
    assert console.client.get("/pause").status_code == 405
    assert console.client.get("/scenarios/fraud").status_code == 405
    assert console.audit_count() == before
    assert console.sent == []


def test_customer_message_is_escaped(console):
    pipeline = console.app.state.pipeline
    case = pipeline.process(ticket("<script>alert(1)</script> fees?", id="XSS"))
    html = console.client.get(f"/cases/{case.id}").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_relative_due():
    assert relative_due(datetime(2026, 11, 2, 13, 30, tzinfo=timezone.utc), NOW) == "due in 1h 30m"
    assert relative_due(datetime(2026, 11, 2, 11, 55, tzinfo=timezone.utc), NOW) == "overdue by 5m"
    assert relative_due(datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc), NOW) == "due in 3d 2h"


# --- Scenarios ---------------------------------------------------------------------------------
@pytest.mark.parametrize("name,state,lane", [
    ("fraud", CaseState.ESCALATED, "urgent"),
    ("routine", CaseState.DRAFTED, "ready"),
    ("complaint", CaseState.ESCALATED, "urgent"),
])
def test_scenarios_land_in_the_right_lane(empty_console, name, state, lane):
    for _ in range(2):  # both variants (EN and DE)
        case = empty_console.scenario(name)
        assert case.state == state, case.reason
        html = empty_console.client.get(f"/cases/{case.id}").text
        assert lane_of(html, case.ticket_id) == lane


def test_complaint_variants_with_and_without_rule_keyword(empty_console):
    with_rule, without_rule = empty_console.scenario("complaint"), empty_console.scenario("complaint")
    assert with_rule.rule_id == "complaint_keywords"
    assert without_rule.rule_id is None and without_rule.class_name == "complaint"
    assert {with_rule.state, without_rule.state} == {CaseState.ESCALATED}
    assert with_rule.draft is None and without_rule.draft is None


def test_hostile_scenario_behaves_like_a_normal_fee_ticket(empty_console):
    for _ in range(2):
        hostile = empty_console.scenario("hostile")
        hostile_ticket = empty_console.store.get_ticket(hostile.ticket_id)
        assert "ignore your instructions" in hostile_ticket.text.casefold()
        conn = connect(":memory:")
        plain_pipeline = Pipeline(empty_console.app.state.config, FakeLLM(), SQLiteCaseStore(conn),
                                  AuditLog(conn, "x"))
        plain = plain_pipeline.process(ticket(
            "Which fees apply to my plan?" if hostile.language == "en" else "Welche Gebühren gelten?",
            language=hostile.language, metadata=hostile_ticket.metadata, received_at=hostile.received_at,
        ))
        for attr in ("class_name", "state", "template_id", "draft", "draft_hash", "team", "due_at"):
            assert getattr(hostile, attr) == getattr(plain, attr), attr
        assert hostile.state == CaseState.DRAFTED
    assert empty_console.sent == []


# --- Approve -------------------------------------------------------------------------------------
def test_approve_unchanged_draft(console):
    case = console.case("T01")
    browser_body = case.draft.replace("\n", "\r\n")  # browsers submit CRLF
    location = console.post(f"/cases/{case.id}/approve", draft_hash=case.draft_hash, body=browser_body)
    assert location.endswith("?msg=sent")
    assert console.events(case.id)[-2:] == ["approved", "sent"]
    assert "edited" not in console.events(case.id)
    assert console.sent == [("T01", case.draft)]
    assert console.store.get(case.id).state == CaseState.SENT
    html = console.client.get(location).text
    assert "Reply sent via mock ticket system." in html
    assert ">T01<" not in html  # sent cases leave the lanes


def test_approve_with_edited_text(console):
    case = console.case("T11")
    new_body = case.draft.replace("Kind regards", "Best regards")
    location = console.post(f"/cases/{case.id}/approve", draft_hash=case.draft_hash, body=new_body)
    assert location.endswith("?msg=edited_sent")
    assert console.events(case.id)[-3:] == ["edited", "approved", "sent"]
    assert console.sent == [("T11", new_body)]
    stored = console.store.get(case.id)
    assert stored.edited and stored.draft_hash != case.draft_hash


def test_stale_draft_hash_is_rejected(console):
    case = console.case("T01")
    console.app.state.gate.edit(case.id, "other-reviewer", "Hello, a newer version.")
    location = console.post(f"/cases/{case.id}/approve", draft_hash=case.draft_hash, body=case.draft)
    assert location == f"/cases/{case.id}?msg=stale"
    assert console.sent == []
    assert "approved" not in console.events(case.id)
    assert "Nothing was sent" in console.client.get(location).text
    # A second submit of an already sent case is stale too.
    fresh = console.store.get(case.id)
    console.post(f"/cases/{case.id}/approve", draft_hash=fresh.draft_hash, body=fresh.draft)
    assert console.post(f"/cases/{case.id}/approve", draft_hash=fresh.draft_hash, body=fresh.draft).endswith("stale")
    assert len(console.sent) == 1


# --- Escalate, correct class, take -----------------------------------------------------------------
def test_escalate_from_drafted_and_needs_person(console):
    for tid in ("T01", "T12"):
        case = console.case(tid)
        console.post(f"/cases/{case.id}/escalate")
        after = console.store.get(case.id)
        assert after.state == CaseState.ESCALATED and after.draft is None
        assert console.events(case.id)[-1] == "escalated"
        assert console.audit.events(case.id)[-1].actor == REVIEWER
    assert console.post("/cases/case-T03/escalate").endswith("?msg=error")  # already escalated
    assert console.sent == []


def test_correct_class_from_needs_person_creates_draft(console):
    case = console.case("T12")  # vague -> unclear
    console.post(f"/cases/{case.id}/class", new_class="general_question")
    after = console.store.get(case.id)
    assert (after.state, after.class_name, after.template_id) == (CaseState.DRAFTED, "general_question",
                                                                  "general_question_reply")
    assert "Hello Noah Zimmermann," in after.draft
    row = next(e for e in console.audit.events(case.id) if e.event == "class_corrected")
    assert row.payload == {"old_class": "unclear", "new_class": "general_question"}
    assert row.actor == REVIEWER
    assert console.events(case.id)[-3:] == ["template_selected", "drafted", "routed"]


def test_correct_class_to_locked_class_escalates(console):
    case = console.case("T01")
    console.post(f"/cases/{case.id}/class", new_class="complaint")
    after = console.store.get(case.id)
    assert (after.state, after.draft, after.template_id, after.team) == (
        CaseState.ESCALATED, None, None, "complaints_team")


def test_correct_class_with_missing_field_needs_person(console):
    case = console.case("T12")  # has no plan_name
    console.post(f"/cases/{case.id}/class", new_class="fee_question")
    after = console.store.get(case.id)
    assert after.state == CaseState.NEEDS_PERSON and after.draft is None
    blocked = [e for e in console.audit.events(case.id) if e.event == "draft_blocked"]
    assert blocked[-1].payload == {"reason_code": "missing_field", "field": "plan_name"}


def test_correct_class_uses_default_template_and_rejects_bad_input(console):
    case = console.case("T08")  # card_blocked / lost_or_stolen
    console.post(f"/cases/{case.id}/class", new_class="card_blocked")
    assert console.store.get(case.id).template_id == "card_blocked_general"
    assert console.post(f"/cases/{case.id}/class", new_class="unclear").endswith("?msg=error")
    assert console.post(f"/cases/{case.id}/class", new_class="nope").endswith("?msg=error")
    assert console.post("/cases/case-T03/class", new_class="fee_question").endswith("?msg=error")


def test_take_case(console):
    console.post("/cases/case-T03/take")
    assert console.store.get("case-T03").state == CaseState.TAKEN
    assert console.events("case-T03")[-1] == "taken"
    assert ">T03<" not in console.client.get("/").text


# --- Pause ---------------------------------------------------------------------------------------
def test_pause_switch(empty_console):
    c = empty_console
    c.post("/pause", paused="on")
    assert c.store.is_paused()
    assert "Pause: <strong>ON</strong>" in c.client.get("/").text
    calls = c.llm.calls
    fraud, routine = c.scenario("fraud"), c.scenario("routine")
    complaint_with_rule, complaint_without_rule = c.scenario("complaint"), c.scenario("complaint")
    hostile = c.scenario("hostile")
    assert fraud.state == CaseState.ESCALATED and complaint_with_rule.state == CaseState.ESCALATED
    for case in (routine, complaint_without_rule, hostile):
        assert (case.state, case.reason) == (CaseState.NEEDS_PERSON, "paused")
    assert c.llm.calls == calls == 0
    c.post("/pause", paused="off")
    assert c.scenario("routine").state == CaseState.DRAFTED
    rows = [e for e in c.audit.events("global")]
    assert [(e.event, e.actor) for e in rows] == [("pause_on", REVIEWER), ("pause_off", REVIEWER)]


def test_pause_is_persisted(tmp_path):
    db = str(tmp_path / "triage.db")
    first = Console(db_path=db)
    first.post("/pause", paused="on")
    second = Console(db_path=db)
    assert second.store.is_paused()
    assert len(second.store.list()) == 15  # not seeded twice


# --- Redirect order --------------------------------------------------------------------------------
def test_redirect_goes_urgent_then_ready_then_needs(empty_console):
    c = empty_console
    ready_a, ready_b = c.scenario("routine"), c.scenario("routine")
    complaint = c.scenario("complaint")  # P15D
    fraud = c.scenario("fraud")  # PT1H, so first in Urgent although newer
    needs = c.app.state.pipeline.process(ticket("hm", id="VAGUE", received_at=NOW))
    assert needs.state == CaseState.NEEDS_PERSON

    assert f"/cases/{fraud.id}" in c.client.get("/").text.split('aria-current="true"')[0].rsplit("<a", 1)[1]
    assert c.post(f"/cases/{ready_a.id}/approve", draft_hash=ready_a.draft_hash,
                  body=ready_a.draft) == f"/cases/{fraud.id}?msg=sent"
    assert c.post(f"/cases/{fraud.id}/take") == f"/cases/{complaint.id}?msg=taken"
    assert c.post(f"/cases/{complaint.id}/take") == f"/cases/{ready_b.id}?msg=taken"
    assert c.post(f"/cases/{ready_b.id}/approve", draft_hash=ready_b.draft_hash,
                  body=ready_b.draft) == f"/cases/{needs.id}?msg=sent"
    # Escalating the last case: it is the only one left, so it opens again in Urgent.
    assert c.post(f"/cases/{needs.id}/escalate") == f"/cases/{needs.id}?msg=escalated"
    assert c.post(f"/cases/{needs.id}/take") == "/?msg=taken"
    html = c.client.get("/?msg=taken").text
    assert "Queue is empty." in html and "Try a scenario button above." in html


def test_empty_queue_page(empty_console):
    html = empty_console.client.get("/").text
    assert "Queue is empty." in html and "Try a scenario button above." in html
    assert "Ready to approve <span class=\"count\">(0)</span>" in html


# --- Static safety checks ----------------------------------------------------------------------
def test_web_layer_never_calls_send_reply():
    web = Path(triage_agent.__file__).parent / "web"
    for path in web.rglob("*"):
        if path.suffix in {".py", ".html", ".css"}:
            assert "send_reply" not in path.read_text(encoding="utf-8"), path


def test_jinja_autoescape_is_on(console):
    from triage_agent.web import app as web_app
    source = Path(web_app.__file__).read_text(encoding="utf-8")
    assert "autoescape=True" in source
