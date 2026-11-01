"""Triage pipeline: rules -> (LLM classify) -> pick template -> fill template.

The pipeline only produces cases and drafts. It has no ticket source and cannot send anything;
sending happens only in gate.py after a recorded human approval.
"""

from __future__ import annotations

from pydantic import ValidationError

from .audit import AuditLog
from .config import UNCLEAR, ClassConfig, Config
from .llm import LLMProvider
from .models import Case, CaseState, ClassifierResult, Ticket, text_hash
from .rules import match_rules
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
        self.store.save(case)
        self.audit.write(case.id, ACTOR, "received", ticket_id=ticket.id, lang=ticket.language)

        escalate_by_rule = self._classify(ticket, case)
        cls = self.config.classes[case.class_name]
        case.team = cls.team
        case.priority = cls.priority
        case.due_at = case.received_at + cls.deadline_delta

        if cls.locked or escalate_by_rule:
            self._finish(case, CaseState.ESCALATED, "locked_class" if cls.locked else "rule_escalation")
        elif cls.draft_mode != "template":
            self._finish(case, CaseState.NEEDS_PERSON, "no_draft_class")
        else:
            self._draft(ticket, case, cls)
        return case

    def _classify(self, ticket: Ticket, case: Case) -> bool:
        """Set class on the case. Returns True when a rule demands escalation."""
        hit = match_rules(ticket, self.config)
        if hit is not None:
            case.class_name, case.rule_id = hit.class_name, hit.rule.id
            case.reason = f"rule {hit.rule.id}"
            self.audit.write(case.id, ACTOR, "rule_matched", rule_id=hit.rule.id, **{"class": hit.class_name})
            return hit.rule.action == "escalate"

        classes = [c for c in self.config.classes.values() if c.name != UNCLEAR]
        try:
            raw = self.llm.classify(ticket.text, classes)
            result = raw if isinstance(raw, ClassifierResult) else ClassifierResult.model_validate(raw)
            # Re-validate even real instances (model_construct could skip checks).
            result = ClassifierResult.model_validate(result.model_dump())
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
        )
        return False

    def _model_error(self, case: Case, exc: Exception, reason_code: str) -> bool:
        case.class_name = UNCLEAR
        case.reason = f"model error ({reason_code})"
        self.audit.write(case.id, ACTOR, "model_error", error_type=type(exc).__name__, reason_code=reason_code)
        return False

    def _draft(self, ticket: Ticket, case: Case, cls: ClassConfig) -> None:
        ref = next((t for t in cls.templates if case.subtype and t.when == case.subtype), None)
        ref = ref or next(t for t in cls.templates if t.default)
        case.template_id = ref.id
        self.audit.write(case.id, ACTOR, "template_selected", template_id=ref.id, subtype=case.subtype)

        template = self.config.template(ref.id, ticket.language)
        if template is None:
            case.reason += f"; no {ticket.language} template"
            self.audit.write(case.id, ACTOR, "draft_blocked", reason_code="missing_language", lang=ticket.language)
            self._finish(case, CaseState.NEEDS_PERSON, "missing_language")
            return
        allowed = set(self.config.settings.allowed_fields)
        try:
            body = fill(template, ticket.metadata, allowed)
        except MissingField as exc:
            case.reason += f"; missing field {exc.field}"
            self.audit.write(case.id, ACTOR, "draft_blocked", reason_code="missing_field", field=exc.field)
            self._finish(case, CaseState.NEEDS_PERSON, "missing_field")
            return
        case.draft = body.rstrip("\n") + "\n\n" + self.config.settings.ai_disclosure[ticket.language] + "\n"
        case.draft_hash = text_hash(case.draft)
        self.audit.write(case.id, ACTOR, "drafted", template_id=ref.id, draft_hash=case.draft_hash)
        self._finish(case, CaseState.DRAFTED, "drafted")

    def _finish(self, case: Case, state: CaseState, reason_code: str) -> None:
        case.transition(state)
        self.store.save(case)
        self.audit.write(
            case.id, ACTOR, "routed", state=state.value, team=case.team, reason_code=reason_code,
            **{"class": case.class_name},
        )
