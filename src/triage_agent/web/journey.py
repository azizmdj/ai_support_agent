"""The six-step trail shown on each decision card, derived only from the case and its audit rows."""

from __future__ import annotations

from dataclasses import dataclass

from ..audit import AuditEvent
from ..config import UNCLEAR
from ..models import Case, CaseState

DONE, SKIPPED, YOUR_TURN, PENDING = "Done", "Skipped", "Your turn", "Pending"

# (name, actor, one-line description for the generic strip)
STEPS = [
    ("Message received", "Customer", "A customer writes to the bank."),
    ("Rules", "Code", "Keyword rules catch fraud and complaints first."),
    ("Classify", "AI", "The model labels what the rules did not catch."),
    ("Template", "Code", "An approved template is filled from ticket data."),
    ("Review", "Reviewer", "A person approves, edits, escalates or takes the case."),
    ("Reply", "Customer", "The approved reply is sent, with an AI-assistance note."),
]

_NO_DRAFT = {
    "locked_class": "locked class",
    "rule_escalation": "rule escalation",
    "paused": "paused",
    "no_draft_class": "class has no template",
}


@dataclass(frozen=True)
class Step:
    number: int
    name: str
    actor: str
    result: str
    status: str


def generic_journey() -> list[Step]:
    return [Step(i, name, actor, desc, PENDING) for i, (name, actor, desc) in enumerate(STEPS, 1)]


def _step(number: int, result: str, status: str) -> Step:
    name, actor, _ = STEPS[number - 1]
    return Step(number, name, actor, result, status)


def journey(case: Case, rows: list[AuditEvent]) -> list[Step]:
    by_event: dict[str, list[AuditEvent]] = {}
    for row in rows:
        by_event.setdefault(row.event, []).append(row)

    def first(event: str) -> AuditEvent | None:
        return by_event.get(event, [None])[0]

    def last(event: str) -> AuditEvent | None:
        return by_event.get(event, [None])[-1]

    received = case.received_at.strftime("%Y-%m-%d %H:%M UTC")
    steps = [_step(1, f"received {received}", DONE)]

    # 2. Rules
    rule, paused = first("rule_matched"), first("held_paused")
    if paused and not rule:
        steps.append(_step(2, "held: paused", DONE))
    elif rule:
        steps.append(_step(2, f"rule {rule.payload.get('rule_id')} fired", DONE))
    else:
        steps.append(_step(2, "no rule matched", DONE))

    # 3. Classify
    classified, model_error = first("classified"), first("model_error")
    if rule:
        classify = _step(3, "skipped: rule fired", SKIPPED)
    elif paused:
        classify = _step(3, "skipped: paused", SKIPPED)
    elif first("mask_failed"):
        classify = _step(3, "failed: masking failed, no model call", DONE)
    elif model_error:
        classify = _step(3, "failed: model error", DONE)
    elif classified:
        label, conf = classified.payload.get("class"), classified.payload.get("confidence")
        text = f"{label} {conf:.2f}" if isinstance(conf, (int, float)) else str(label)
        if classified.payload.get("reason_code") == "low_confidence":
            text += " (low confidence)"
        if classified.payload.get("provider"):
            text += f" via {classified.payload['provider']}"
        classify = _step(3, text, DONE)
    else:
        classify = _step(3, "not run yet", PENDING)
    corrected = last("class_corrected")
    if corrected:
        classify = Step(3, classify.name, classify.actor,
                        f"{classify.result}; corrected to {corrected.payload.get('new_class')} by {corrected.actor}",
                        classify.status)
    steps.append(classify)

    # 4. Template: the last outcome wins (a class correction redrafts).
    template = _step(4, "not run yet", PENDING)
    for row in rows:
        if row.event == "drafted":
            template = _step(4, str(row.payload.get("template_id")), DONE)
        elif row.event == "draft_blocked":
            if row.payload.get("reason_code") == "missing_field":
                why = f"missing {row.payload.get('field')}"
            else:
                why = f"no {row.payload.get('lang')} template"
            template = _step(4, f"no draft: {why}", SKIPPED)
        elif row.event == "routed" and row.payload.get("reason_code") in _NO_DRAFT:
            why = _NO_DRAFT[row.payload["reason_code"]]
            if row.payload.get("reason_code") == "no_draft_class" and row.payload.get("class") == UNCLEAR:
                why = "class unclear"
            template = _step(4, f"no draft: {why}", SKIPPED)
    steps.append(template)

    # 5. Review and 6. Reply
    state = case.state
    if state == CaseState.SENT:
        approved = last("approved")
        who = approved.actor if approved else case.reviewer_id
        verb = "edited and approved" if first("edited") else "approved"
        steps.append(_step(5, f"{verb} by {who}", DONE))
        steps.append(_step(6, "sent, with AI-assistance note", DONE))
    elif state == CaseState.TAKEN:
        steps.append(_step(5, f"taken by {case.reviewer_id}", DONE))
        steps.append(_step(6, "no reply: handled by a person", SKIPPED))
    elif state == CaseState.ESCALATED:
        by = last("escalated")
        text = f"escalated by {by.actor}: waiting for Take case" if by else "escalated: waiting for Take case"
        steps.append(_step(5, text, YOUR_TURN))
        steps.append(_step(6, "no reply: handled by a person", SKIPPED))
    elif state == CaseState.DRAFTED:
        steps.append(_step(5, "waiting: approve, edit or escalate" + (" (edited)" if case.edited else ""), YOUR_TURN))
        steps.append(_step(6, "not sent yet", PENDING))
    elif state == CaseState.NEEDS_PERSON:
        steps.append(_step(5, "waiting: pick a class or escalate", YOUR_TURN))
        steps.append(_step(6, "not sent yet", PENDING))
    else:
        steps.append(_step(5, "waiting", PENDING))
        steps.append(_step(6, "not sent yet", PENDING))
    return steps
