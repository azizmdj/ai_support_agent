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
from triage_agent.web.journey import STEPS

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
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "Unofficial demo, synthetic data" in header
    assert '<a href="/" aria-current="page">Review</a>' in header and '<a href="/system" >System</a>' in header
    assert "<button" not in header and "<form" not in header  # the header holds only the name and links
    status = html.split('<div class="status">', 1)[1].split("</div>", 1)[0]  # sidebar footer
    assert 'Model: <a href="/system#settings">FakeLLM</a>' in status
    assert f"Config <code>{console.app.state.config.version}</code>" in status
    assert "Pause: <strong>OFF</strong>" in status
    assert status.count("<button") == 1 and 'action="/pause"' in status  # the pause switch lives here
    assert '<a href="/system">System</a>' not in status  # the header link is the only System link
    assert '<a class="new" href="/new" >New message</a>' in html  # a case is open, so not the current page
    assert '<details class="queue" open>' in html and "<summary>Queue (15)</summary>" in html
    assert 'class="pill"' not in html  # test-message buttons live on /new only
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
    pages = ["/", "/?msg=sent", "/new", "/system", "/health", "/static/style.css"]
    pages += [f"/cases/{c.id}" for c in console.store.list()]
    for url in pages:
        assert console.client.get(url).status_code == 200, url
    for action in ("approve", "escalate", "take", "class"):
        assert console.client.get(f"/cases/case-T01/{action}").status_code == 405
    assert console.client.get("/pause").status_code == 405
    assert console.client.get("/scenarios/fraud").status_code == 405
    assert console.audit_count() == before
    assert console.sent == []


def test_message_reason_and_payload_text_are_escaped(console):
    case = console.app.state.pipeline.process(ticket("<script>alert(1)</script> fees?", id="XSS"))
    case.reason = "<script>alert(2)</script>"
    console.store.save(case)
    console.audit.write(case.id, "pipeline", "drafted", template_id="<b>tpl</b>")
    html = console.client.get(f"/cases/{case.id}").text
    for raw in ("<script>alert(1)</script>", "<script>alert(2)</script>", "<b>tpl</b>"):
        assert raw not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&lt;b&gt;tpl&lt;/b&gt;" in html


@pytest.mark.parametrize("state,primary", [
    ("drafted", "Approve and send"), ("needs_person", "Set class and draft"), ("escalated", "Take case"),
])
def test_each_card_has_exactly_one_primary_button(console, state, primary):
    case = next(c for c in console.store.list() if c.state.value == state)
    html = console.client.get(f"/cases/{case.id}").text
    assert html.count('class="primary"') == 1
    assert f'<button type="submit" class="primary">{primary}</button>' in html
    secondary = html.split("<summary>Something wrong?</summary>", 1)
    if state == "escalated":
        assert len(secondary) == 1  # no secondary actions on an escalated case
    else:
        assert "Escalate to a person" in secondary[1].split("</details>", 1)[0]


def test_trail_links_to_system_anchors(console):
    html = console.client.get("/cases/case-T01").text
    for name, anchor, _, _ in STEPS:
        assert f'href="/system#{anchor}">' in html and f". {name}</a>" in html
    for actor in ("Customer", "Code", "AI", "Reviewer"):
        assert f'<span class="actor">{actor}</span>' in html
    card = html.split('<article class="card', 1)[1]
    # Order: message, collapsed Steps, draft and the one primary button, then "Something wrong?".
    order = ['class="message"', '<details class="steps">', 'class="draft"', 'class="primary"', "Something wrong?"]
    assert [card.index(x) for x in order] == sorted(card.index(x) for x in order)
    steps = card.split('<details class="steps">', 1)[1]
    summary = steps.split("<summary>", 1)[1].split("</summary>", 1)[0]
    assert summary.startswith("Steps · ") and summary.endswith(" · Your turn")
    assert "state <strong>drafted</strong>" in steps and "team " in steps  # the facts live inside Steps
    assert "What each step saw and produced" in steps and "Names and free-text addresses are not masked." in steps
    assert "<summary>Audit log (" in steps.split("What each step saw and produced", 1)[1]  # nested, collapsed
    assert '<details class="steps" open' not in html
    assert '<p class="entry">' not in html and '<p class="reason">' not in html  # hint and old Why line are gone


def test_steps_summary_carries_the_reason_and_status(console):
    escalated = console.client.get("/cases/case-T03").text
    assert "<summary>Steps · rule fraud_keywords · Your turn</summary>" in escalated
    case = console.case("T01")
    console.post(f"/cases/{case.id}/approve", draft_hash=case.draft_hash, body=case.draft)
    sent = console.client.get(f"/cases/{case.id}").text
    assert "· No action needed</summary>" in sent and "Status: <strong>Your turn</strong>" not in sent


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


def test_needs_person_class_select_starts_on_placeholder_and_rejects_it(console):
    case = console.case("T12")  # needs_person
    html = console.client.get(f"/cases/{case.id}").text
    select = html.split('<select id="set_class"', 1)[1].split("</select>", 1)[0]
    assert '<option value="" disabled selected>Choose a class...</option>' in select
    assert select.count("selected") == 1  # no real class preselected
    before, state = console.audit_count(), console.store.get(case.id)
    for data in ({}, {"new_class": ""}, {"new_class": "Choose a class..."}):
        resp = console.client.post(f"/cases/{case.id}/class", data=data)
        assert resp.status_code == 303 and resp.headers["location"].endswith("?msg=error")
    assert console.store.get(case.id) == state and console.audit_count() == before
    assert "Not done:</strong>" in console.client.get(f"/cases/{case.id}?msg=error").text


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
    assert "Done:</strong> Case taken." in html and "Queue is empty. Send a test message" in html


def home_main(html):
    return html.split('<main class="main">', 1)[1].split("</main>", 1)[0]


def test_empty_queue_page(empty_console):
    html = empty_console.client.get("/").text  # an empty queue renders the home screen
    main = home_main(html)
    assert "Queue is empty. Send a test message to see" in main
    assert '<a href="/system#flow">how a ticket moves</a>' in main
    assert main.count('class="pill"') == 4 and "<article" not in main and 'class="primary"' not in main
    assert "Ready to approve <span class=\"count\">(0)</span>" in html


def test_new_message_home_screen(console):
    before = console.audit_count()
    html = console.client.get("/new").text
    main = home_main(html)
    assert main.lstrip().startswith('<div class="home">') and "<article" not in main
    assert "Queue is empty" not in main and "Send a test message to see" in main
    for s in ("routine", "fraud", "complaint", "hostile"):
        assert f'class="pill" formaction="/scenarios/{s}"' in main
    assert '<a class="new" href="/new" aria-current="page">New message</a>' in html
    assert "<summary>Queue (15)</summary>" in html and 'aria-current="true"' not in html  # sidebar, no case open
    assert console.client.post("/new").status_code == 405
    assert console.audit_count() == before


# --- Static safety checks ----------------------------------------------------------------------
def test_web_layer_never_calls_send_reply():
    web = Path(triage_agent.__file__).parent / "web"
    for path in web.rglob("*"):
        if path.suffix in {".py", ".html", ".css"}:
            assert "send_reply" not in path.read_text(encoding="utf-8"), path


# --- Free-text composer (only on /new) ----------------------------------------------------------
def typed(console, text, language="en"):
    location = console.post("/messages", text=text, language=language)
    case_id = re.match(r"/cases/([^?]+)\?msg=message$", location).group(1)
    return console.store.get(case_id)


def composer(html):
    return html.split('<form class="composer"', 1)[1].split("</form>", 1)[0]


def test_typed_messages_land_in_the_right_lane(empty_console):
    c = empty_console
    fraud = typed(c, "Someone made a payment I did not make. I think it is fraud.")
    routine = typed(c, "How do I download my invoice for October?")
    assert fraud.ticket_id.startswith("M-") and routine.ticket_id.startswith("M-")
    assert (fraud.state, fraud.rule_id) == (CaseState.ESCALATED, "fraud_keywords") and fraud.draft is None
    assert routine.state == CaseState.DRAFTED and "Demo Customer" in routine.draft  # the fixed fake name
    assert c.store.get_ticket(routine.ticket_id).metadata == {"customer_name": "Demo Customer"}
    html = c.client.get("/new").text
    assert lane_of(html, fraud.ticket_id) == "urgent" and lane_of(html, routine.ticket_id) == "ready"
    assert "Test message created and triaged." in c.client.get(f"/cases/{routine.id}?msg=message").text
    assert c.sent == []


def test_typed_message_with_a_missing_field_needs_a_person(empty_console):
    case = typed(empty_console, "I lost my card on the train, it may have been stolen.")
    assert case.state == CaseState.NEEDS_PERSON and case.draft is None
    assert "missing field card_last4" in case.reason  # only the name is filled in; card_last4 is not invented


@pytest.mark.parametrize("text,language", [
    ("", "en"), ("   \n\t  ", "en"), ("x" * 2001, "en"), ("How do I get my invoice?", "fr"),
    ("How do I get my invoice?", ""), ("How do I get my invoice?", "<script>"),
])
def test_bad_typed_input_creates_nothing(empty_console, text, language):
    c = empty_console
    before = c.audit_count()
    assert c.post("/messages", text=text, language=language) == "/new?msg=invalid_message"
    assert c.store.list() == [] and c.audit_count() == before
    assert "Not done:</strong> Nothing was created." in c.client.get("/new?msg=invalid_message").text


def test_typed_text_limits_and_missing_fields(empty_console):
    c = empty_console
    assert c.client.post("/messages", data={}).headers["location"] == "/new?msg=invalid_message"  # not a 422
    exact = typed(c, "  " + "a" * 1990 + " fraud  ")  # trimmed to 1996 characters, within the limit
    assert c.store.get_ticket(exact.ticket_id).text == "a" * 1990 + " fraud"
    crlf = typed(c, "How do I get my invoice?\r\nThanks", language="de")
    assert c.store.get_ticket(crlf.ticket_id).text == "How do I get my invoice?\nThanks" and crlf.language == "de"


def test_get_messages_is_not_allowed(empty_console):
    assert empty_console.client.get("/messages").status_code == 405
    assert empty_console.store.list() == []


def test_typed_text_is_escaped_and_never_audited_or_in_a_url(empty_console):
    c = empty_console
    marker = "zq-marker-7f3"
    text = f"<script>alert(4)</script> how do i pay {marker}"
    location = c.post("/messages", text=text, language="en")
    assert marker not in location and "script" not in location
    case = c.store.get(re.match(r"/cases/([^?]+)", location).group(1))
    html = c.client.get(location).text
    assert "<script>alert(4)</script>" not in html and "&lt;script&gt;alert(4)&lt;/script&gt;" in html
    rows = c.app.state.store.conn.execute("SELECT payload_json FROM audit").fetchall()
    assert rows and not any(marker in r[0] or "alert(4)" in r[0] for r in rows)
    assert [e.event for e in c.audit.events(case.id)][0] == "received"


def test_composer_markup_on_new(console):
    html = console.client.get("/new").text
    form = composer(html)
    submits = re.findall(r"<button[^>]*>", form)
    assert len(submits) == 5 and ">Send</button>" in form
    assert submits[0] == '<button type="submit" class="send-button">'  # Enter triggers Send, not a pill
    assert all("formnovalidate" in b and 'class="pill"' in b for b in submits[1:])
    assert 'class="primary"' not in html  # no card, so no primary button
    assert 'placeholder="Send a test message..."' in form and 'maxlength="2000"' in form and "required" in form
    options = re.findall(r'<option value="([^"]+)">', form)
    assert options[0] == "en" and set(options) == set(console.app.state.config.settings.ai_disclosure)
    assert "Synthetic data only. Don't type real personal data. Names and addresses are not masked." in html
    assert "<article" not in html


def test_composer_is_absent_on_case_pages(console):
    for case in console.store.list():
        html = console.client.get(f"/cases/{case.id}").text
        assert 'action="/messages"' not in html and "Send a test message..." not in html


def test_css_has_dark_mode_and_no_external_resources(console):
    css = console.client.get("/static/style.css").text
    assert "@media (prefers-color-scheme: dark)" in css
    assert "url(" not in css and "@import" not in css and "@keyframes" not in css and "transition" not in css
