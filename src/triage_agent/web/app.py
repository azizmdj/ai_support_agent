"""Reviewer console: one server-rendered page, plain forms, no JavaScript.

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

from .. import DEFAULT_PACK, DEFAULT_TICKETS
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
from .journey import generic_journey, journey

HERE = Path(__file__).parent

MESSAGES = {
    "sent": "Reply sent via mock ticket system.",
    "edited_sent": "Edited reply sent via mock ticket system. The edit and the approval are both logged.",
    "escalated": "Case escalated. It is now in the Urgent lane.",
    "corrected": "Class corrected. The case moved to the lane for its new class.",
    "taken": "Case taken. It left the queue.",
    "paused": "Triage paused. New tickets go to a person unless a rule escalates them.",
    "resumed": "Triage resumed.",
    "scenario": "New scenario ticket created and triaged.",
    "stale": "This draft changed or was already handled. Nothing was sent. Check the current version below.",
    "error": "That action is not allowed for this case. Nothing was sent.",
}

LANES = [
    ("urgent", "Urgent", CaseState.ESCALATED),
    ("ready", "Ready to approve", CaseState.DRAFTED),
    ("needs", "Needs a person", CaseState.NEEDS_PERSON),
]


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


def create_app(
    pack: Path = DEFAULT_PACK,
    db_path: str | None = None,
    reviewer_id: str | None = None,
    llm: LLMProvider | None = None,
    seed_tickets: Path | None = DEFAULT_TICKETS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FastAPI:
    config = load_config(pack)
    scenarios = {s.id: s for s in load_scenarios(pack)}
    conn = connect(db_path or os.environ.get("TRIAGE_DB", ":memory:"))
    store, audit = SQLiteCaseStore(conn), AuditLog(conn, config.version)
    source = MockTicketSource([])
    llm = llm or build_llm(config)  # fails at startup with a clear message if a key variable is unset
    model_label = llm.describe() if isinstance(llm, FallbackChain) else type(llm).__name__
    model_ids = llm.ids if isinstance(llm, FallbackChain) else [type(llm).__name__]
    pipeline = Pipeline(config, llm, store, audit)
    gate = Gate(store, audit, source, config)
    reviewer = reviewer_id or os.environ.get("TRIAGE_REVIEWER", "demo-reviewer")
    lock = threading.Lock()  # one SQLite connection, used by one request at a time
    rotation = {name: 0 for name in scenarios}

    if seed_tickets is not None and not store.list():
        tickets = load_tickets(seed_tickets)
        shift = now() - max(t.received_at for t in tickets)  # newest fixture arrives "just now"
        for t in tickets:
            pipeline.process(t.model_copy(update={"received_at": t.received_at + shift}))

    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True)
    app = FastAPI(title="Support triage console (unofficial demo)", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.store, app.state.audit, app.state.gate, app.state.source = store, audit, gate, source
    app.state.pipeline, app.state.config, app.state.reviewer = pipeline, config, reviewer

    def lanes() -> dict[str, list[Case]]:
        cases = store.list()
        far = datetime.max.replace(tzinfo=timezone.utc)
        return {
            key: sorted((c for c in cases if c.state == state), key=lambda c: (c.due_at or far, c.id))
            for key, _, state in LANES
        }

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

    def render(selected: Case | None, msg: str | None) -> HTMLResponse:
        current = now()
        ticket = store.get_ticket(selected.ticket_id) if selected else None
        all_lanes = lanes()
        events = audit.events(selected.id) if selected else []
        html = env.get_template("console.html").render(
            reviewer=reviewer,
            paused=store.is_paused(),
            scenarios=list(scenarios.values()),
            lanes=[(key, title, all_lanes[key]) for key, title, _ in LANES],
            selected=selected,
            ticket=ticket,
            events=events,
            model_label=model_label,
            masked=masked_view(ticket.text) if ticket else None,
            model_called=any(e.event in {"classified", "model_error"} for e in events),
            steps=journey(selected, events) if selected else generic_journey(),
            classes=[c for c in config.classes.values() if c.name != UNCLEAR],
            due=lambda c: relative_due(c.due_at, current),
            banner=MESSAGES.get(msg or ""),
            banner_is_error=msg in {"stale", "error"},
        )
        return HTMLResponse(html)

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

    @app.get("/", response_class=HTMLResponse)
    def index(msg: str | None = None):
        with lock:
            target = next_case_id()
            return render(store.get(target) if target else None, msg)

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
    def correct_class(case_id: str, new_class: str = Form(...)):
        return _act(case_id, lambda: gate.correct_class(case_id, reviewer, new_class), "corrected")

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

    @app.post("/scenarios/{name}")
    def run_scenario(name: str):
        scenario = scenarios.get(name)
        if scenario is None:
            raise HTTPException(status_code=404, detail="unknown scenario")
        with lock:
            variant = scenario.variants[rotation[name] % len(scenario.variants)]
            rotation[name] += 1
            ticket = Ticket(
                id=f"S-{name}-{uuid.uuid4().hex[:6]}",
                received_at=now(),
                language=variant.language,
                text=variant.text,
                metadata=variant.metadata,
            )
            case = pipeline.process(ticket)
            return back(case.id, "scenario")

    return app
