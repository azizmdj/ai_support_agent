"""Human reviewer actions. approve() is the only code allowed to send a reply to a customer."""

from __future__ import annotations

from .audit import AuditLog
from .config import UNCLEAR, Config
from .models import Case, CaseState, text_hash
from .pipeline import apply_class, default_or_subtype, draft_reply, finish
from .store import CaseStore
from .ticket_source import TicketSource

GLOBAL_CASE_ID = "global"  # audit rows that belong to no single case, such as the pause switch


class GateError(Exception):
    pass


class Gate:
    def __init__(self, store: CaseStore, audit: AuditLog, source: TicketSource, config: Config | None = None):
        self.store = store
        self.audit = audit
        self.source = source
        self.config = config

    @staticmethod
    def _require_reviewer(reviewer_id: str | None) -> None:
        if not reviewer_id or not reviewer_id.strip():
            raise GateError("reviewer_id is required")

    def _load(self, case_id: str, reviewer_id: str, *states: CaseState) -> Case:
        self._require_reviewer(reviewer_id)
        case = self.store.get(case_id)
        if case.state not in states:
            raise GateError(f"{case_id} is {case.state.value}, expected {' or '.join(s.value for s in states)}")
        return case

    def approve(self, case_id: str, reviewer_id: str, draft_hash: str) -> Case:
        case = self._load(case_id, reviewer_id, CaseState.DRAFTED)
        if not case.draft or draft_hash != case.draft_hash or text_hash(case.draft) != case.draft_hash:
            raise GateError(f"{case_id}: draft hash does not match the current draft")
        self.audit.write(case.id, reviewer_id, "approved", draft_hash=case.draft_hash, template_id=case.template_id)
        self.source.send_reply(case.ticket_id, case.draft)
        case.reviewer_id = reviewer_id
        case.transition(CaseState.SENT)
        self.store.save(case)
        self.audit.write(case.id, reviewer_id, "sent", draft_hash=case.draft_hash, ticket_id=case.ticket_id)
        return case

    def edit(self, case_id: str, reviewer_id: str, new_body: str) -> Case:
        case = self._load(case_id, reviewer_id, CaseState.DRAFTED)
        if not new_body.strip():
            raise GateError("edited draft is empty")
        old_hash = case.draft_hash
        case.draft = new_body
        case.draft_hash = text_hash(new_body)
        case.edited = True
        self.store.save(case)
        self.audit.write(case.id, reviewer_id, "edited", old_hash=old_hash, new_hash=case.draft_hash)
        return case

    def take(self, case_id: str, reviewer_id: str) -> Case:
        case = self._load(case_id, reviewer_id, CaseState.ESCALATED)
        case.reviewer_id = reviewer_id
        case.transition(CaseState.TAKEN)
        self.store.save(case)
        self.audit.write(case.id, reviewer_id, "taken", **{"class": case.class_name})
        return case

    def escalate(self, case_id: str, reviewer_id: str) -> Case:
        case = self._load(case_id, reviewer_id, CaseState.DRAFTED, CaseState.NEEDS_PERSON)
        case.draft, case.draft_hash = None, None
        case.reason += f"; escalated by {reviewer_id}"
        case.transition(CaseState.ESCALATED)
        self.store.save(case)
        self.audit.write(case.id, reviewer_id, "escalated", **{"class": case.class_name})
        return case

    def correct_class(self, case_id: str, reviewer_id: str, new_class: str) -> Case:
        """Set the right class (labeled feedback) and redraft from that class's default template."""
        case = self._load(case_id, reviewer_id, CaseState.DRAFTED, CaseState.NEEDS_PERSON)
        if self.config is None:
            raise GateError("correct_class needs the config")
        cls = self.config.classes.get(new_class)
        if cls is None or cls.name == UNCLEAR:
            raise GateError(f"unknown class {new_class!r}")
        self.audit.write(case.id, reviewer_id, "class_corrected", old_class=case.class_name, new_class=cls.name)
        apply_class(case, cls)
        case.subtype, case.confidence, case.rule_id = None, None, None
        case.draft, case.draft_hash, case.edited, case.template_id = None, None, False, None
        case.reason = f"class set by {reviewer_id}: {cls.name}"
        if cls.locked:
            state, reason_code = CaseState.ESCALATED, "locked_class"
        elif cls.draft_mode != "template":
            state, reason_code = CaseState.NEEDS_PERSON, "no_draft_class"
        else:
            ticket = self.store.get_ticket(case.ticket_id)
            state, reason_code = draft_reply(self.config, ticket, case, default_or_subtype(cls, None),
                                             self.audit, reviewer_id)
        finish(case, state, reason_code, self.store, self.audit, reviewer_id)
        return case

    def set_paused(self, reviewer_id: str, paused: bool) -> None:
        """Pause switch: while on, the LLM is not called and only rule escalations bypass the human queue."""
        self._require_reviewer(reviewer_id)
        if self.store.is_paused() == paused:
            return
        self.store.set_paused(paused)
        self.audit.write(GLOBAL_CASE_ID, reviewer_id, "pause_on" if paused else "pause_off")
