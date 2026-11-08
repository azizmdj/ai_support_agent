"""The six-step trail shown on each decision card, derived only from the case, its audit rows and the
loaded config. Nothing here is stored; the audit log never holds message text."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audit import AuditEvent
from ..config import UNCLEAR
from ..models import Case, CaseState

DONE, SKIPPED, YOUR_TURN, PENDING = "Done", "Skipped", "Your turn", "Pending"

# (name, anchor on /system, actor, one-line description)
STEPS = [
    ("Message received", "received", "Customer", "A customer writes to the bank."),
    ("Rules", "rules", "Code", "Keyword rules catch fraud and complaints first."),
    ("Classify", "classify", "AI", "The model labels what the rules did not catch."),
    ("Template", "template", "Code", "An approved template is filled from ticket data."),
    ("Review", "review", "Reviewer", "A person approves, edits, escalates or takes the case."),
    ("Reply", "reply", "Code", "The approved reply is sent, with an AI-assistance note."),
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
    anchor: str
    actor: str
    result: str
    status: str
    input: str = ""
    config: str = ""


@dataclass(frozen=True)
class Context:
    """Read-only facts from the loaded config, used for the input and config columns."""

    threshold: float | None = None
    rule_count: int | None = None
    providers: dict[str, str] = field(default_factory=dict)  # provider id -> "model x, temperature y"
    chain: str = ""
    masked: str | None = None  # computed on the fly from the stored ticket, never stored
    reviewer: str = ""


def _step(number: int, result: str, status: str, input: str = "", config: str = "") -> Step:
    name, anchor, actor, _ = STEPS[number - 1]
    return Step(number, name, anchor, actor, result, status, input, config)


def journey(case: Case, rows: list[AuditEvent], ctx: Context | None = None) -> list[Step]:
    ctx = ctx or Context()
    by_event: dict[str, list[AuditEvent]] = {}
    for row in rows:
        by_event.setdefault(row.event, []).append(row)

    def first(event: str) -> AuditEvent | None:
        return by_event.get(event, [None])[0]

    def last(event: str) -> AuditEvent | None:
        return by_event.get(event, [None])[-1]

    threshold = f"threshold {ctx.threshold}" if ctx.threshold is not None else "threshold from settings.yaml"
    rules_cfg = f"rules.yaml ({ctx.rule_count} rules)" if ctx.rule_count is not None else "rules.yaml"

    received = case.received_at.strftime("%Y-%m-%d %H:%M UTC")
    steps = [_step(1, f"received {received}", DONE, input="customer message from the ticket system",
                   config=f"ticket {case.ticket_id}, language {case.language}")]

    # 2. Rules
    rule, paused = first("rule_matched"), first("held_paused")
    rules_input = "the message text (case and accents ignored)"
    if rule:
        steps.append(_step(2, f"rule {rule.payload.get('rule_id')} fired", DONE, rules_input, rules_cfg))
    elif paused:
        steps.append(_step(2, "held: paused", DONE, rules_input, f"{rules_cfg}; pause switch on"))
    else:
        steps.append(_step(2, "no rule matched", DONE, rules_input, rules_cfg))

    # 3. Classify
    classified, model_error = first("classified"), first("model_error")
    model_input = f"the masked message: {ctx.masked}" if ctx.masked is not None else "the masked message"
    chain_cfg = f"chain {ctx.chain}, {threshold}" if ctx.chain else threshold
    if rule:
        classify = _step(3, "skipped: rule fired", SKIPPED, "nothing: no model call", "-")
    elif paused:
        classify = _step(3, "skipped: paused", SKIPPED, "nothing: no model call", "pause switch on")
    elif first("mask_failed"):
        classify = _step(3, "failed: masking failed, no model call", DONE, "nothing: masking failed", chain_cfg)
    elif model_error:
        classify = _step(3, "failed: model error", DONE, model_input, chain_cfg)
    elif classified:
        p = classified.payload
        label, conf, provider = p.get("class"), p.get("confidence"), p.get("provider")
        text = f"{label} {conf:.2f}" if isinstance(conf, (int, float)) else str(label)
        if p.get("reason_code") == "low_confidence":
            text += " (low confidence)"
        if provider:
            text += f" via {provider}"
        output_detail = f"; subtype {p['subtype']}" if p.get("subtype") else ""
        model_cfg = ctx.providers.get(provider or "", "")
        cfg = ", ".join(x for x in (model_cfg, threshold) if x)
        classify = _step(3, text + output_detail, DONE, model_input, cfg)
    else:
        classify = _step(3, "not run yet", PENDING)
    corrected = last("class_corrected")
    if corrected:
        classify = Step(3, classify.name, classify.anchor, classify.actor,
                        f"{classify.result}; corrected to {corrected.payload.get('new_class')} by {corrected.actor}",
                        classify.status, classify.input, classify.config)
    steps.append(classify)

    # 4. Template: the last outcome wins (a class correction redrafts).
    tpl_input = f"class {case.class_name}, language {case.language}, ticket fields"
    tpl_cfg = "taxonomy.yaml, templates/, allowed fields in settings.yaml"
    template = _step(4, "not run yet", PENDING)
    for row in rows:
        if row.event == "drafted":
            template = _step(4, str(row.payload.get("template_id")), DONE, tpl_input, tpl_cfg)
        elif row.event == "draft_blocked":
            if row.payload.get("reason_code") == "missing_field":
                why = f"missing {row.payload.get('field')}"
            else:
                why = f"no {row.payload.get('lang')} template"
            template = _step(4, f"no draft: {why}", SKIPPED, tpl_input, tpl_cfg)
        elif row.event == "routed" and row.payload.get("reason_code") in _NO_DRAFT:
            why = _NO_DRAFT[row.payload["reason_code"]]
            if row.payload.get("reason_code") == "no_draft_class" and row.payload.get("class") == UNCLEAR:
                why = "class unclear"
            if rule and row.payload.get("reason_code") in {"locked_class", "rule_escalation"}:
                # The pipeline records the locked class first, but what the reader needs is that a rule fired.
                template = _step(4, f"no draft: rule {rule.payload.get('rule_id')} fired", SKIPPED, tpl_input,
                                 rules_cfg)
                continue
            template = _step(4, f"no draft: {why}", SKIPPED, tpl_input, "risk flag and draft mode in taxonomy.yaml")
    steps.append(template)

    # 5. Review and 6. Reply
    gate_cfg = f"approval gate, reviewer {ctx.reviewer}" if ctx.reviewer else "approval gate"
    review_input = f"draft {case.draft_hash[:8]}" if case.draft_hash else "the case, without a draft"
    reply_cfg = f"AI-disclosure line ({case.language}) from settings.yaml"
    state = case.state
    if state == CaseState.SENT:
        approved = last("approved")
        who = approved.actor if approved else case.reviewer_id
        verb = "edited and approved" if first("edited") else "approved"
        steps.append(_step(5, f"{verb} by {who}", DONE, review_input, gate_cfg))
        steps.append(_step(6, "sent, with AI-assistance note", DONE, "the approved draft", reply_cfg))
    elif state == CaseState.TAKEN:
        steps.append(_step(5, f"taken by {case.reviewer_id}", DONE, review_input, gate_cfg))
        steps.append(_step(6, "no reply: handled by a person", SKIPPED, "-", "-"))
    elif state == CaseState.ESCALATED:
        by = last("escalated")
        text = f"escalated by {by.actor}: waiting for Take case" if by else "escalated: waiting for Take case"
        steps.append(_step(5, text, YOUR_TURN, review_input, gate_cfg))
        steps.append(_step(6, "no reply: handled by a person", SKIPPED, "-", "-"))
    elif state == CaseState.DRAFTED:
        text = "waiting: approve, edit or escalate" + (" (edited)" if case.edited else "")
        steps.append(_step(5, text, YOUR_TURN, review_input, gate_cfg))
        steps.append(_step(6, "not sent yet", PENDING, "the approved draft", reply_cfg))
    elif state == CaseState.NEEDS_PERSON:
        steps.append(_step(5, "waiting: pick a class or escalate", YOUR_TURN, review_input, gate_cfg))
        steps.append(_step(6, "not sent yet", PENDING, "the approved draft", reply_cfg))
    else:
        steps.append(_step(5, "waiting", PENDING))
        steps.append(_step(6, "not sent yet", PENDING))
    return steps
