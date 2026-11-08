"""Reviewer console: a Review page and a read-only System page, plain forms, no JavaScript.

GET requests only read. Every action is a POST that calls the Gate and then redirects to the next case.
The web layer never talks to the ticket system directly; replies leave only through Gate.approve.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from .. import DEFAULT_PACK, DEFAULT_TICKETS, REPO_ROOT
from ..audit import AuditLog
from ..config import UNCLEAR, load_config
from ..gate import Gate, GateError
from ..llm import FallbackChain, LLMProvider
from ..masking import mask
from ..models import Case, CaseState, Ticket, normalize_newlines
from ..pipeline import Pipeline
from ..providers.registry import build_llm
from ..store import CaseNotFound, SQLiteCaseStore, connect
from ..ticket_source import MockTicketSource, load_tickets
from . import monitor, system
from .journey import Context, ai_decision, journey

HERE = Path(__file__).parent

MESSAGES = {
    "sent": "Reply sent via mock ticket system.",
    "edited_sent": "Edited reply sent via mock ticket system. The edit and the approval are both logged.",
    "escalated": "Case escalated. It is now in the Urgent lane.",
    "corrected": "Class corrected. The case moved to the lane for its new class.",
    "redrafted": "Class changed. A new draft was made from the new class's template.",
    "corrected_stayed": "Class changed, but no draft could be made. The case stays in Needs a person.",
    "taken": "Case taken. It left the queue.",
    "paused": "Triage paused. New tickets go to a person unless a rule escalates them.",
    "resumed": "Triage resumed.",
    "scenario": "New scenario ticket created and triaged.",
    "message": "Test message created and triaged.",
    "invalid_message": "Nothing was created. Type 1 to 2000 characters and pick a listed language.",
    "stale": "This draft changed or was already handled. Nothing was sent. Check the current version below.",
    "error": "That action is not allowed for this case. Nothing was sent.",
}

MAX_MESSAGE_CHARS = 2000
SEED_TIME_SCALE = 0.25  # fixture ages are compressed to a quarter when the console seeds an empty database
DEMO_CUSTOMER = {"customer_name": "Demo Customer"}  # fake name, so templates can greet a typed message

LANES = [
    ("urgent", "Urgent", CaseState.ESCALATED),
    ("ready", "Ready to approve", CaseState.DRAFTED),
    ("needs", "Needs a person", CaseState.NEEDS_PERSON),
]
DONE_STATES = {CaseState.SENT: "sent", CaseState.TAKEN: "taken"}  # the Done lane: closed, newest first


class ScenarioVariant(BaseModel):
    language: str
    text: str
    metadata: dict[str, str] = {}


class Scenario(BaseModel):
    id: str
    label: str
    variants: list[ScenarioVariant]


def load_scenarios(pack: Path) -> list[Scenario]:
    data = yaml.safe_load((pack / "scenarios.yaml").read_text(encoding="utf-8"))
    return [Scenario.model_validate(s) for s in data["scenarios"]]


def relative_due(due: datetime | None, now: datetime) -> str:
    if due is None:
        return "no deadline"
    seconds = int((due - now).total_seconds())
    minutes = abs(seconds) // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    span = f"{days}d {hours}h" if days else f"{hours}h {mins}m" if hours else f"{mins}m"
    return f"due in {span}" if seconds >= 0 else f"overdue by {span}"


def short_due(due: datetime | None, now: datetime) -> str:
    """One unit only, for the queue: "due in 2h", "1h late", "3d late"."""
    if due is None:
        return "no deadline"
    seconds = int((due - now).total_seconds())
    minutes = abs(seconds) // 60
    span = f"{minutes // 1440}d" if minutes >= 1440 else f"{minutes // 60}h" if minutes >= 60 else f"{minutes}m"
    return f"due in {span}" if seconds >= 0 else f"{span} late"


BLOCKED_NOTES = {
    "missing_field": "A required detail is missing, so no draft can be made. Ask the customer or escalate.",
    "missing_language": "There is no approved template in this language, so no draft can be made. Escalate.",
}


def blocked_note(events: list) -> str | None:
    """Why the latest draft attempt was blocked, if it was. A different class cannot fix this, so the card
    offers Escalate as the main action instead of "Set class and draft"."""
    for e in reversed(events):
        if e.event == "draft_blocked":
            return BLOCKED_NOTES.get(e.payload.get("reason_code"))
        if e.event in {"drafted", "class_corrected"}:
            return None
    return None


def class_label(name: str | None) -> str:
    return (name or "no class").replace("_", " ")


def queue_line(case: Case, now: datetime) -> str:
    """One short line per queue item, e.g. "T03 · fraud report · 1h late"; a closed case shows its outcome."""
    tail = DONE_STATES.get(case.state) or short_due(case.due_at, now)
    return f"{case.ticket_id} · {class_label(case.class_name)} · {tail}"


def create_app(
    pack: Path = DEFAULT_PACK,
    db_path: str | None = None,
    reviewer_id: str | None = None,
    llm: LLMProvider | None = None,
    seed_tickets: Path | None = DEFAULT_TICKETS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    eval_results: Path = REPO_ROOT / "eval" / "results",
) -> FastAPI:
    config = load_config(pack)
    scenarios = {s.id: s for s in load_scenarios(pack)}
    conn = connect(db_path or os.environ.get("TRIAGE_DB", ":memory:"))
    store, audit = SQLiteCaseStore(conn), AuditLog(conn, config.version)
    source = MockTicketSource([])
    llm = llm or build_llm(config)  # fails at startup with a clear message if a key variable is unset
    model_ids = llm.ids if isinstance(llm, FallbackChain) else [type(llm).__name__]
    model_label = ", then ".join(system.provider_label(config, pid) for pid in model_ids)
    pipeline = Pipeline(config, llm, store, audit)
    gate = Gate(store, audit, source, config)
    reviewer = reviewer_id or os.environ.get("TRIAGE_REVIEWER", "demo-reviewer")
    lock = threading.Lock()  # one SQLite connection, used by one request at a time
    rotation = {name: 0 for name in scenarios}
    languages = sorted(config.settings.ai_disclosure, key=lambda lang: (lang != "en", lang))  # en first: default

    if seed_tickets is not None and not store.list():
        tickets = load_tickets(seed_tickets)
        # The newest fixture arrives "just now"; older ones keep their order with ages scaled down, so the
        # first screen shows due-soon cases and only one clearly late fraud report. Deadlines are unchanged.
        newest, start = max(t.received_at for t in tickets), now()
        for t in tickets:
            received = start - (newest - t.received_at) * SEED_TIME_SCALE
            pipeline.process(t.model_copy(update={"received_at": received}))

    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True)
    app = FastAPI(title="Support triage console (unofficial demo)", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.store, app.state.audit, app.state.gate, app.state.source = store, audit, gate, source
    app.state.pipeline, app.state.config, app.state.reviewer = pipeline, config, reviewer

    def lanes() -> dict[str, list[Case]]:
        cases = store.list()
        far = datetime.max.replace(tzinfo=timezone.utc)
        open_lanes = {
            key: sorted((c for c in cases if c.state == state), key=lambda c: (c.due_at or far, c.id))
            for key, _, state in LANES
        }
        done = sorted((c for c in cases if c.state in DONE_STATES), key=lambda c: c.received_at, reverse=True)
        return {**open_lanes, "done": done}

    def next_case_id(skip: str | None = None) -> str | None:
        """Urgent first, then Ready, then Needs a person. Prefer a case other than the one just handled."""
        all_lanes = lanes()
        ordered = [c for key, _, _ in LANES for c in all_lanes[key]]
        others = [c for c in ordered if c.id != skip] or ordered
        return others[0].id if others else None

    def go_next(msg: str, skip: str | None = None) -> RedirectResponse:
        target = next_case_id(skip)
        url = f"/cases/{target}?msg={msg}" if target else f"/?msg={msg}"
        return RedirectResponse(url, status_code=303)

    def back(case_id: str, msg: str) -> RedirectResponse:
        return RedirectResponse(f"/cases/{case_id}?msg={msg}", status_code=303)

    def page(template: str, name: str, msg: str | None = None, **context) -> HTMLResponse:
        header = dict(page=name, model_label=model_label, config_version=config.version, paused=store.is_paused(),
                      banner=MESSAGES.get(msg or ""), banner_is_error=msg in {"stale", "error", "invalid_message"})
        return HTMLResponse(env.get_template(template).render(**header, **context))

    trail_context = dict(threshold=config.settings.confidence_threshold, rule_count=len(config.rules),
                         providers={pid: system.model_summary(config, pid) for pid in model_ids},
                         chain=" -> ".join(model_ids), reviewer=reviewer,
                         provider_labels={pid: system.provider_label(config, pid) for pid in model_ids})

    def render(selected: Case | None, msg: str | None) -> HTMLResponse:
        current = now()
        ticket = store.get_ticket(selected.ticket_id) if selected else None
        all_lanes = lanes()
        events = audit.events(selected.id) if selected else []
        steps, ai = [], None
        if selected:
            model_called = any(e.event in {"classified", "model_error"} for e in events)
            masked = masked_view(ticket.text) if model_called else None
            ctx = Context(masked=masked, **trail_context)
            steps, ai = journey(selected, events, ctx), ai_decision(selected, events, ctx)
        return page(
            "review.html", "review", msg,
            scenarios=list(scenarios.values()),
            languages=languages,
            max_chars=MAX_MESSAGE_CHARS,
            lanes=[(key, title, all_lanes[key], True) for key, title, _ in LANES]
            + [("done", "Done", all_lanes["done"], bool(selected and selected.state in DONE_STATES))],
            total=sum(len(all_lanes[key]) for key, _, _ in LANES),
            selected=selected,
            ticket=ticket,
            events=events,
            steps=steps,
            ai=ai,
            classes=[c for c in config.classes.values() if c.name != UNCLEAR],
            due=lambda c: relative_due(c.due_at, current),
            line=lambda c: queue_line(c, current),
            blocked=blocked_note(events) if selected and selected.state == CaseState.NEEDS_PERSON else None,
        )

    def masked_view(text: str) -> str:
        """What a real model sees. Computed on the fly from the stored ticket, never stored."""
        try:
            return mask(text)[0]
        except Exception:
            return "(masking failed: nothing would be sent to a model)"

    def get_case(case_id: str) -> Case:
        try:
            return store.get(case_id)
        except CaseNotFound:
            raise HTTPException(status_code=404, detail="case not found") from None

    # --- Pages (read only) ------------------------------------------------------------------------
    @app.get("/health")
    def health():
        """Liveness check for containers: provider ids only, no state change, no secrets."""
        return {"status": "ok", "models": model_ids}

    @app.get("/system", response_class=HTMLResponse)
    def system_page():
        """The Setup tab. Read-only; the prompt is rendered with a placeholder and no model is called."""
        prompt = system.classifier_prompt(config)
        with lock:
            return page(
                "system.html", "system",
                flow=system.flow_rows(config, model_ids),
                threshold=config.settings.confidence_threshold,
                unclear_deadline=system.human_duration(config.settings.unclear_deadline),
                reviewer=reviewer,
                providers=system.provider_rows(config, model_ids, os.environ),
                classes=system.class_rows(config),
                rules=system.rule_rows(config),
                deadlines={name: system.human_duration(cls.deadline) for name, cls in config.classes.items()},
                templates=system.template_rows(config),
                allowed_fields=config.settings.allowed_fields,
                disclosure=sorted(config.settings.ai_disclosure.items()),
                prompt_system=prompt[0],
                prompt_user=prompt[1],
                transitions=system.transitions(),
            )

    @app.get("/monitor", response_class=HTMLResponse)
    def monitor_page():
        """Read-only overview: counts, overdue cases, the latest eval report and recent audit rows."""
        with lock:
            cases, current = store.list(), now()
            return page(
                "monitor.html", "monitor",
                states=monitor.state_counts(cases),
                late=monitor.overdue(cases, current),
                by_class=monitor.class_counts(cases),
                report=monitor.latest_eval(eval_results),
                events=audit.recent(20),
                line=lambda c: queue_line(c, current),
            )

    @app.get("/", response_class=HTMLResponse)
    def index(msg: str | None = None):
        with lock:
            target = next_case_id()
            return render(store.get(target) if target else None, msg)

    @app.get("/new", response_class=HTMLResponse)
    def new_message(msg: str | None = None):
        """Home screen: only the test-message buttons. Reads only."""
        with lock:
            return render(None, msg)

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def show_case(case_id: str, msg: str | None = None):
        with lock:
            return render(get_case(case_id), msg)

    # --- Actions (POST only) -----------------------------------------------------------------------
    @app.post("/cases/{case_id}/approve")
    def approve(case_id: str, draft_hash: str = Form(...), body: str = Form(...)):
        with lock:
            case = get_case(case_id)
            if case.state != CaseState.DRAFTED or draft_hash != case.draft_hash:
                return back(case_id, "stale")
            edited = normalize_newlines(body).rstrip() != normalize_newlines(case.draft or "").rstrip()
            try:
                if edited:
                    draft_hash = gate.edit(case_id, reviewer, body).draft_hash or ""
                gate.approve(case_id, reviewer, draft_hash)
            except GateError:
                return back(case_id, "error")
            return go_next("edited_sent" if edited else "sent", skip=case_id)

    @app.post("/cases/{case_id}/escalate")
    def escalate(case_id: str):
        return _act(case_id, lambda: gate.escalate(case_id, reviewer), "escalated")

    @app.post("/cases/{case_id}/take")
    def take(case_id: str):
        return _act(case_id, lambda: gate.take(case_id, reviewer), "taken")

    @app.post("/cases/{case_id}/class")
    def correct_class(case_id: str, new_class: str = Form("")):
        """A missing or placeholder class gets the error banner (not a 422); the gate rejects it before any write.
        "Moved lane" is only said when the state really changed; otherwise the same case opens again."""
        with lock:
            before = get_case(case_id).state
            try:
                after = gate.correct_class(case_id, reviewer, new_class).state
            except GateError:
                return back(case_id, "error")
            if after != before:
                return go_next("corrected", skip=case_id)
            return back(case_id, "redrafted" if after == CaseState.DRAFTED else "corrected_stayed")

    def _act(case_id: str, action: Callable[[], object], msg: str) -> RedirectResponse:
        with lock:
            get_case(case_id)
            try:
                action()
            except GateError:
                return back(case_id, "error")
            return go_next(msg, skip=case_id)

    @app.post("/pause")
    def pause(paused: str = Form(...)):
        with lock:
            on = paused == "on"
            gate.set_paused(reviewer, on)
            return go_next("paused" if on else "resumed")

    def create_ticket(ticket_id: str, language: str, text: str, metadata: dict[str, str]) -> Case:
        """One path for every test message: build the ticket and run it through the pipeline."""
        ticket = Ticket(id=ticket_id, received_at=now(), language=language, text=text, metadata=metadata)
        return pipeline.process(ticket)

    @app.post("/scenarios/{name}")
    def run_scenario(name: str):
        scenario = scenarios.get(name)
        if scenario is None:
            raise HTTPException(status_code=404, detail="unknown scenario")
        with lock:
            variant = scenario.variants[rotation[name] % len(scenario.variants)]
            rotation[name] += 1
            case = create_ticket(f"S-{name}-{uuid.uuid4().hex[:6]}", variant.language, variant.text, variant.metadata)
            return back(case.id, "scenario")

    @app.post("/messages")
    def send_message(text: str = Form(""), language: str = Form("")):
        """A typed test message. Checked here, not by Form limits, so a bad input gets a banner, not a 422.
        The text never goes into a URL; the redirect carries only a message key."""
        text = normalize_newlines(text).strip()
        if not 1 <= len(text) <= MAX_MESSAGE_CHARS or language not in languages:
            return RedirectResponse("/new?msg=invalid_message", status_code=303)
        with lock:
            case = create_ticket(f"M-{uuid.uuid4().hex[:6]}", language, text, dict(DEMO_CUSTOMER))
            return back(case.id, "message")

    return app
