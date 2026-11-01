"""Triage pipeline: rules -> (LLM classify) -> pick template -> fill template.

The pipeline only produces cases and drafts. It has no ticket source and cannot send anything;
sending happens only in gate.py after a recorded human approval.
"""

from __future__ import annotations

from pydantic import ValidationError

from .audit import AuditLog
from .config import UNCLEAR, ClassConfig, Config, TemplateRef
from .llm import LLMProvider
from .masking import MaskingError
from .models import Case, CaseState, ClassifierResult, Ticket, text_hash
from .rules import RuleHit, match_rules
from .store import CaseStore
from .templates import MissingField, fill

ACTOR = "pipeline"


class Pipeline:
    def __init__(self, config: Config, llm: LLMProvider, store: CaseStore, audit: AuditLog):
        self.config = config
        self.llm = llm
        self.store = store
        self.audit = audit

    def process(self, ticket: Ticket) -> Case:
        case = Case(
            id=f"case-{ticket.id}",
            ticket_id=ticket.id,
            language=ticket.language,
            received_at=ticket.received_at,
            config_version=self.config.version,
        )
        self.store.save_ticket(ticket)
        self.store.save(case)
        self.audit.write(case.id, ACTOR, "received", ticket_id=ticket.id, lang=ticket.language)

        outcome = self._classify(ticket, case)
        cls = self.config.classes[case.class_name]
        apply_class(case, cls)

        if cls.locked or outcome == "escalate":
            self._finish(case, CaseState.ESCALATED, "locked_class" if cls.locked else "rule_escalation")
        elif outcome == "paused":
            self._finish(case, CaseState.NEEDS_PERSON, "paused")
        elif cls.draft_mode != "template":
            self._finish(case, CaseState.NEEDS_PERSON, "no_draft_class")
        else:
            state, reason_code = draft_reply(self.config, ticket, case, default_or_subtype(cls, case.subtype),
                                             self.audit, ACTOR)
            self._finish(case, state, reason_code)
        return case

    def _classify(self, ticket: Ticket, case: Case) -> str | None:
        """Set class on the case. Returns "escalate" (rule demands it), "paused" or None."""
        hit = match_rules(ticket, self.config)
        if hit is not None and hit.rule.action == "escalate":
            return self._rule_hit(case, hit)
        if self.store.is_paused():
            # Pause switch: no model call, everything a rule does not escalate goes to a person.
            case.class_name, case.reason = UNCLEAR, "paused"
            self.audit.write(case.id, ACTOR, "held_paused", reason_code="paused")
            return "paused"
        if hit is not None:
            return self._rule_hit(case, hit)

        classes = [c for c in self.config.classes.values() if c.name != UNCLEAR]
        try:
            raw = self.llm.classify(ticket.text, classes)
            result = raw if isinstance(raw, ClassifierResult) else ClassifierResult.model_validate(raw)
            # Re-validate even real instances (model_construct could skip checks).
            result = ClassifierResult.model_validate(result.model_dump())
        except MaskingError as exc:
            # Fail closed: masking failed, so no model was called. A person handles the case.
            case.class_name, case.reason = UNCLEAR, "masking failed: no model call"
            self.audit.write(case.id, ACTOR, "mask_failed", error_type=type(exc).__name__, reason_code="mask_failed")
            return None
        except (ValidationError, TypeError, ValueError) as exc:
            return self._model_error(case, exc, "invalid_output")
        except Exception as exc:  # provider down, timeout, anything
            return self._model_error(case, exc, "provider_error")

        case.confidence = result.confidence
        cls = self.config.classes.get(result.label)
        if cls is None or cls.name == UNCLEAR:
            case.class_name, reason_code = UNCLEAR, "unknown_label" if cls is None else "model_unclear"
            case.reason = f"model: no usable class ({reason_code})"
        elif result.confidence < self.config.settings.confidence_threshold:
            case.class_name, reason_code = UNCLEAR, "low_confidence"
            case.reason = f"low confidence: {result.label} {result.confidence:.2f}"
        else:
            case.class_name, reason_code = cls.name, "model"
            case.subtype = result.subtype if result.subtype in cls.subtypes else None
            case.reason = f"model: {cls.name} {result.confidence:.2f}"
        self.audit.write(
            case.id, ACTOR, "classified",
            confidence=result.confidence, reason_code=reason_code, subtype=case.subtype,
            **{"class": case.class_name},
            **({"provider": result.provider} if result.provider else {}),
        )
        return None

    def _rule_hit(self, case: Case, hit: RuleHit) -> str | None:
        case.class_name, case.rule_id = hit.class_name, hit.rule.id
        case.reason = f"rule {hit.rule.id}"
        self.audit.write(case.id, ACTOR, "rule_matched", rule_id=hit.rule.id, **{"class": hit.class_name})
        return "escalate" if hit.rule.action == "escalate" else None

    def _model_error(self, case: Case, exc: Exception, reason_code: str) -> None:
        case.class_name = UNCLEAR
        case.reason = f"model error ({reason_code})"
        self.audit.write(case.id, ACTOR, "model_error", error_type=type(exc).__name__, reason_code=reason_code)
        return None

    def _finish(self, case: Case, state: CaseState, reason_code: str) -> None:
        finish(case, state, reason_code, self.store, self.audit, ACTOR)


def apply_class(case: Case, cls: ClassConfig) -> None:
    case.class_name = cls.name
    case.team = cls.team
    case.priority = cls.priority
    case.due_at = case.received_at + cls.deadline_delta


def default_or_subtype(cls: ClassConfig, subtype: str | None) -> TemplateRef:
    ref = next((t for t in cls.templates if subtype and t.when == subtype), None)
    return ref or next(t for t in cls.templates if t.default)


def draft_reply(
    config: Config, ticket: Ticket, case: Case, ref: TemplateRef, audit: AuditLog, actor: str
) -> tuple[CaseState, str]:
    """Fill the template into case.draft. Returns the target state and a reason code. Never sends."""
    case.draft, case.draft_hash, case.edited = None, None, False
    case.template_id = ref.id
    audit.write(case.id, actor, "template_selected", template_id=ref.id, subtype=case.subtype)

    template = config.template(ref.id, ticket.language)
    if template is None:
        case.reason += f"; no {ticket.language} template"
        audit.write(case.id, actor, "draft_blocked", reason_code="missing_language", lang=ticket.language)
        return CaseState.NEEDS_PERSON, "missing_language"
    try:
        body = fill(template, ticket.metadata, set(config.settings.allowed_fields))
    except MissingField as exc:
        case.reason += f"; missing field {exc.field}"
        audit.write(case.id, actor, "draft_blocked", reason_code="missing_field", field=exc.field)
        return CaseState.NEEDS_PERSON, "missing_field"
    case.draft = body.rstrip("\n") + "\n\n" + config.settings.ai_disclosure[ticket.language] + "\n"
    case.draft_hash = text_hash(case.draft)
    audit.write(case.id, actor, "drafted", template_id=ref.id, draft_hash=case.draft_hash)
    return CaseState.DRAFTED, "drafted"


def finish(case: Case, state: CaseState, reason_code: str, store: CaseStore, audit: AuditLog, actor: str) -> None:
    case.transition(state)
    store.save(case)
    audit.write(
        case.id, actor, "routed", state=state.value, team=case.team, reason_code=reason_code,
        **{"class": case.class_name},
    )
