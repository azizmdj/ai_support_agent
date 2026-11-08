import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import ticket
from fastapi.testclient import TestClient

import triage_agent
from triage_agent.audit import AuditLog
from triage_agent.llm import FakeLLM
from triage_agent.models import CaseState, ClassifierResult
from triage_agent.pipeline import Pipeline
from triage_agent.store import SQLiteCaseStore, connect
from triage_agent.web.app import create_app, relative_due, short_due
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


LANE_KEYS = ("urgent", "ready", "needs")


def lane_html(html, key):
    return html.split(f'class="lane lane-{key}"', 1)[1].split("</details>", 1)[0]


def in_queue(html, ticket_id):
    return any(f">{ticket_id} · " in lane_html(html, key) for key in LANE_KEYS)


def lane_of(html, ticket_id):
    for key in LANE_KEYS:
        if f">{ticket_id} · " in lane_html(html, key):
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


def test_seeded_queue_has_at_most_one_overdue_case(console):
    overdue = [c for c in console.store.list() if c.due_at < NOW]
    assert [(c.ticket_id, c.class_name) for c in overdue] == [("T03", "fraud_report")]
    assert NOW - overdue[0].due_at >= timedelta(minutes=15)  # clearly late, not by a minute
    due_soon = [c for c in console.store.list() if NOW <= c.due_at <= NOW + timedelta(hours=4)]
    assert len(due_soon) >= 4  # the other fraud reports and the card cases are due soon
    html = console.client.get("/").text
    assert html.count(" late</a>") == 1


def test_page_renders_lanes_and_header(console):
    html = console.client.get("/").text
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "Unofficial demo, synthetic data" in header
    assert '<a href="/system" >Setup</a>\n    <a href="/" aria-current="page">Review</a>' in header
    assert "<button" not in header and "<form" not in header  # the header holds only the name and links
    status = html.split('<div class="status">', 1)[1].split("</div>", 1)[0]  # sidebar footer
    assert 'Model: <a href="/system#settings">test model (no real AI)</a>' in status
    assert f"Config version <code>{console.app.state.config.version}</code>" in status
    assert "Pause stops the AI for new messages: rules still escalate fraud and complaints" in status
    assert "Pause: <strong>OFF</strong>" in status
    assert status.count("<button") == 1 and 'action="/pause"' in status  # the pause switch lives here
    assert '<a href="/system">System</a>' not in status  # the header link is the only System link
    assert '<a class="new" href="/new" >New message</a>' in html  # a case is open, so not the current page
    assert '<details class="queue" open>' in html and "<summary>Queue (15)</summary>" in html
    assert 'class="pill"' not in html  # test-message buttons live on /new only
    assert '<details class="lane lane-urgent" open>\n          <summary>Urgent (5)</summary>' in html
    assert "<summary>Ready to approve (7)</summary>" in html
    assert "<summary>Needs a person (3)</summary>" in html
    # Default selection is the most urgent case: fraud has the shortest deadline.
    assert 'aria-current="true"' in html
    assert "https://" not in html and "<script" not in html  # no CDN, no JavaScript


def test_get_requests_never_change_state(console):
    before = console.audit_count()
    pages = ["/", "/?msg=sent", "/new", "/system", "/monitor", "/health", "/static/style.css"]
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


@pytest.mark.parametrize("tid,state,primary", [
    ("T01", "drafted", "Approve and send"), ("T12", "needs_person", "Set class and draft"),
    ("T03", "escalated", "Take case"),
])
def test_each_card_has_exactly_one_primary_button(console, tid, state, primary):
    case = console.case(tid)
    assert case.state.value == state
    html = console.client.get(f"/cases/{case.id}").text
    assert html.count('class="primary"') == 1
    assert f'<button type="submit" class="primary">{primary}</button>' in html
    secondary = html.split("<summary>Change class or escalate</summary>", 1)
    if state == "escalated":
        assert len(secondary) == 1  # no secondary actions on an escalated case
    else:
        assert "Escalate to a person" in secondary[1].split("</details>", 1)[0]


def test_missing_field_case_offers_escalate_not_set_class(console):
    case = console.case("T09")  # card_blocked, card_last4 missing
    html = console.client.get(f"/cases/{case.id}").text
    assert "A required detail is missing, so no draft can be made. Ask the customer or escalate." in html
    assert html.count('class="primary"') == 1
    assert '<button type="submit" class="primary">Escalate to a person</button>' in html
    assert "Set class and draft" not in html and 'id="set_class"' not in html
    secondary = html.split("<summary>Change class</summary>", 1)[1].split("</details>", 1)[0]
    assert "Change class" in secondary and "Escalate" not in secondary  # escalate is already the main action


def test_class_banner_only_says_moved_when_the_lane_changed(console):
    case = console.case("T12")  # unclear; has no plan_name
    # A class whose template needs a missing field: the case stays and opens again with an honest banner.
    location = console.post(f"/cases/{case.id}/class", new_class="fee_question")
    assert location == f"/cases/{case.id}?msg=corrected_stayed"
    assert console.store.get(case.id).state == CaseState.NEEDS_PERSON
    html = console.client.get(location).text
    assert "The case stays in Needs a person." in html and "moved to the lane" not in html
    assert "A required detail is missing" in html
    # T09 (PIN template needs card_last4): the class change still works, because the default template does not.
    t09 = console.case("T09")
    assert console.post(f"/cases/{t09.id}/class", new_class="card_blocked").endswith("?msg=corrected")
    assert console.store.get(t09.id).state == CaseState.DRAFTED
    # Drafted to another drafting class: a new draft in the same lane.
    t01 = console.case("T01")
    assert console.post(f"/cases/{t01.id}/class", new_class="general_question") == f"/cases/{t01.id}?msg=redrafted"
    # A real lane change keeps the old banner and moves on.
    assert console.post(f"/cases/{t01.id}/class", new_class="complaint").endswith("?msg=corrected")


def test_trail_links_to_system_anchors(console):
    html = console.client.get("/cases/case-T01").text
    for name, anchor, _, _ in STEPS:
        assert f'href="/system#{anchor}">' in html and f". {name}</a>" in html
    for actor in ("Customer", "Code", "AI", "Reviewer"):
        assert f'<span class="actor">{actor}</span>' in html
    card = html.split('<article class="card', 1)[1]
    # Order: message, collapsed "How this was decided", draft and the one primary button, then the secondary actions.
    order = ['class="message"', 'class="ai-box"', '<details class="steps">', 'class="draft"', 'class="primary"',
             "Change class or escalate"]
    assert [card.index(x) for x in order] == sorted(card.index(x) for x in order)
    steps = card.split('<details class="steps">', 1)[1]
    summary = steps.split("<summary>", 1)[1].split("</summary>", 1)[0]
    assert summary == "How this was decided · model: fee_question 0.90"
    assert card.count("Your turn") == 1  # once per card: the step that waits for the reviewer
    assert "state <strong>drafted</strong>" in steps and "team " in steps  # the facts live inside Steps
    assert "What each step saw and produced" in steps and "Names and free-text addresses are not masked." in steps
    assert "<summary>Audit log (" in steps.split("What each step saw and produced", 1)[1]  # nested, collapsed
    assert '<details class="steps" open' not in html
    assert '<p class="entry">' not in html and '<p class="reason">' not in html  # hint and old Why line are gone


def test_steps_summary_carries_the_reason_and_status(console):
    escalated = console.client.get("/cases/case-T03").text
    assert "<summary>How this was decided · rule fraud_keywords</summary>" in escalated
    assert escalated.count("Your turn") == 1
    case = console.case("T01")
    console.post(f"/cases/{case.id}/approve", draft_hash=case.draft_hash, body=case.draft)
    sent = console.client.get(f"/cases/{case.id}").text
    assert "Your turn" not in sent and "has left the queue" in sent


def test_relative_due():
    assert relative_due(datetime(2026, 11, 2, 13, 30, tzinfo=timezone.utc), NOW) == "due in 1h 30m"
    assert relative_due(datetime(2026, 11, 2, 11, 55, tzinfo=timezone.utc), NOW) == "overdue by 5m"
    assert relative_due(datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc), NOW) == "due in 3d 2h"


def test_short_due_and_queue_line():
    assert short_due(datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc), NOW) == "due in 2h"
    assert short_due(datetime(2026, 11, 2, 10, 40, tzinfo=timezone.utc), NOW) == "1h late"
    assert short_due(datetime(2026, 11, 2, 11, 55, tzinfo=timezone.utc), NOW) == "5m late"
    assert short_due(datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc), NOW) == "due in 3d"
    assert short_due(None, NOW) == "no deadline"


def test_queue_item_is_one_short_line(console):
    html = console.client.get("/").text
    urgent = lane_html(html, "urgent")
    items = re.findall(r"<li[^>]*><a [^>]*>([^<]*)</a></li>", urgent)
    assert len(items) == 5 and all(re.fullmatch(r"T\d\d · [a-z ]+ · (due in \d+[mhd]|\d+[mhd] late)", i) for i in items)
    assert any(" · fraud report · " in i for i in items)  # underscores become spaces
    assert "<span" not in urgent and "Open:" not in urgent  # nothing else in an item


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
    assert not in_queue(html, "T01")  # sent cases leave the open lanes...
    assert ">T01 · fee question · sent</a>" in lane_html(html, "done")  # ...and show up under Done


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
    assert not in_queue(console.client.get("/").text, "T03")


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
    assert "<summary>Ready to approve (0)</summary>" in html


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


# --- AI decision box ---------------------------------------------------------------------------------
def ai_box(html):
    return html.split('<section class="ai-box"', 1)[1].split("</section>", 1)[0]


def test_ai_box_for_a_drafted_case(console):
    box = ai_box(console.client.get("/cases/case-T01").text)
    assert "The AI picked a class with enough confidence." in box
    assert "<dt>Predicted class</dt><dd>fee question</dd>" in box
    assert "<dt>Confidence</dt><dd>0.90, needs 0.70</dd>" in box
    assert "<dt>Answered by</dt>" in box  # the injected test double has no provider id; see the next test
    assert "<dt>The model saw</dt><dd>Hi, which fees apply to my account?" in box


def test_ai_box_names_the_provider_from_the_chain():
    app = create_app(db_path=":memory:", reviewer_id=REVIEWER, now=lambda: NOW)  # default chain: fake
    box = ai_box(TestClient(app).get("/cases/case-T01").text)
    assert "<dt>Answered by</dt><dd>test model (no real AI)</dd>" in box


def test_ai_box_for_a_rule_case_names_the_config_keyword(console):
    box = ai_box(console.client.get("/cases/case-T04").text)  # "BETRUG!!! ..."
    assert 'No AI call: rule fraud_keywords fired on the keyword &#34;betrug&#34; from pack/rules.yaml.' in box
    assert "<dl>" not in box  # one line, no model rows
    regex = console.app.state.pipeline.process(ticket("I did not authorize this payment", id="RX"))
    box = ai_box(console.client.get(f"/cases/{regex.id}").text)
    assert r"on the pattern &#34;re:\bdid not (make|authori[sz]e)\b&#34;" in box


def test_ai_box_keeps_the_predicted_class_for_low_confidence(empty_console):
    case = typed(empty_console, "My transfer is pending and which fees apply?")  # two topics -> 0.5
    assert (case.class_name, case.predicted_class) == ("unclear", "pending_transfer")
    box = ai_box(empty_console.client.get(f"/cases/{case.id}").text)
    assert "The AI was not sure enough, so a person decides." in box
    assert "<dt>Predicted class</dt><dd>pending transfer</dd>" in box and "<dd>0.50, needs 0.70</dd>" in box


def test_ai_box_without_a_model_call(empty_console):
    c = empty_console
    c.post("/pause", paused="on")
    paused = c.scenario("routine")
    assert "No AI call: triage was paused when this message arrived." in ai_box(c.client.get(f"/cases/{paused.id}").text)


# --- Done lane -----------------------------------------------------------------------------------
def test_done_lane_lists_sent_and_taken_cases_collapsed(console):
    html = console.client.get("/").text
    assert '<details class="lane lane-done">' in html and "<summary>Done (0)</summary>" in html
    case = console.case("T01")
    console.post(f"/cases/{case.id}/approve", draft_hash=case.draft_hash, body=case.draft)
    console.post("/cases/case-T03/take")
    html = console.client.get("/cases/case-T04").text
    done = lane_html(html, "done")
    assert '<details class="lane lane-done">' in html and "<summary>Done (2)</summary>" in done
    assert ">T01 · fee question · sent</a>" in done and ">T03 · fraud report · taken</a>" in done
    assert "<summary>Queue (13)</summary>" in html  # the queue count covers open cases only
    # Opening a closed case opens the Done lane, so the selection is visible.
    assert '<details class="lane lane-done" open>' in console.client.get("/cases/case-T01").text


# --- Model explanation (case store only) ---------------------------------------------------------
def test_model_explanation_is_shown_truncated_and_never_audited(empty_console):
    c = empty_console
    marker = "zq-explain-91"
    c.llm._script = [ClassifierResult(label="general_question", confidence=0.95, reason=f"{marker} " + "x" * 400)]
    case = typed(c, "How do I get my invoice?")
    assert case.model_explanation.startswith(marker) and len(case.model_explanation) == 300
    box = ai_box(c.client.get(f"/cases/{case.id}").text)
    assert f"<dt>Model&#39;s explanation (unverified)</dt><dd>{marker} " in box
    rows = c.app.state.store.conn.execute("SELECT payload_json FROM audit").fetchall()
    assert rows and not any(marker in r[0] for r in rows)
    assert marker not in case.draft  # never used in a reply


# --- Monitor -------------------------------------------------------------------------------------
def monitor_html(console):
    return console.client.get("/monitor").text


def test_monitor_shows_counts_overdue_and_nav_order(console):
    html = monitor_html(console)
    nav = html.split('aria-label="Pages">', 1)[1].split("</nav>", 1)[0]
    assert [m for m in re.findall(r">(\w+)</a>", nav)] == ["Setup", "Review", "Monitor"]
    assert 'aria-current="page">Monitor</a>' in nav
    for n, label in ((5, "Urgent (escalated)"), (7, "Ready to approve"), (3, "Needs a person"), (0, "Sent"),
                     (0, "Taken by a person")):
        assert f"<li><strong>{n}</strong> {label}</li>" in html
    assert "<strong>1</strong> Overdue" in html and ">T03 · fraud report · " in html
    assert "<td>fraud_report</td><td>3</td><td>0</td>" in html
    assert "Pause: <strong>OFF</strong>" in html and f"<code>{console.app.state.config.version}</code>" in html
    assert "<form" not in html.split("</header>", 1)[1] and 'class="sidebar"' not in html


def test_monitor_shows_the_latest_eval_report_from_the_repo(console):
    html = monitor_html(console)
    eval_section = html.split('<section id="eval">', 1)[1].split("</section>", 1)[0]
    assert "2026-11-02 · openai_main · test split, 38 tickets" in eval_section  # test wins over dev on a tie
    assert "<td>0 (target 0)</td>" in eval_section and "<td>0 of 19 tickets" in eval_section
    assert "<td>37 of 38</td>" in eval_section and "<code>cb16d991171f</code>" in eval_section
    assert "Eval ran on an older config." in eval_section  # the live config has changed since


def test_monitor_without_a_report_and_with_a_matching_one(tmp_path):
    app = create_app(db_path=":memory:", reviewer_id=REVIEWER, llm=FakeLLM(), now=lambda: NOW,
                     seed_tickets=None, eval_results=tmp_path)
    client = TestClient(app)
    assert "No evaluation report found" in client.get("/monitor").text
    version = app.state.config.version
    (tmp_path / "fake-dev-2026-11-08.md").write_text(
        "# Evaluation: fake, split dev, 2026-11-08\n\n| Setting | Value |\n| --- | --- |\n"
        f"| config_version | `{version}` |\n\n| Metric | Value |\n| --- | --- |\n| tickets | 62 |\n"
        "| class_accuracy | 0.500 |\n| risky_drafted | 2 |\n| pii_tickets | 32 |\n| pii_leak_rate | 0.000 |\n",
        encoding="utf-8")
    html = client.get("/monitor").text
    assert "Eval ran on an older config" not in html and "<td>31 of 62</td>" in html
    assert "regression check, not a result" in html  # a test-model run is labeled as such


def test_monitor_recent_audit_events_are_newest_first_and_hold_no_text(empty_console):
    c = empty_console
    marker = "zq-monitor-55"
    typed(c, f"How do I get my invoice? {marker}")
    c.post("/pause", paused="on")
    html = monitor_html(c)
    audit = html.split('<section id="audit">', 1)[1].split("</section>", 1)[0]
    rows = re.findall(r"<tr>\s*<td>", audit)
    assert len(rows) == min(20, c.audit_count())
    assert audit.index("pause_on") < audit.index("received")  # newest first
    assert marker not in html
