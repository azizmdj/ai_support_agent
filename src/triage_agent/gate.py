"""Human approval gate: the only code allowed to send a reply to a customer."""

from __future__ import annotations

from .audit import AuditLog
from .models import Case, CaseState, text_hash
from .store import CaseStore
from .ticket_source import TicketSource


class GateError(Exception):
    pass


class Gate:
    def __init__(self, store: CaseStore, audit: AuditLog, source: TicketSource):
        self.store = store
        self.audit = audit
        self.source = source

    def _load(self, case_id: str, reviewer_id: str, state: CaseState) -> Case:
        if not reviewer_id or not reviewer_id.strip():
            raise GateError("reviewer_id is required")
        case = self.store.get(case_id)
        if case.state != state:
            raise GateError(f"{case_id} is {case.state.value}, expected {state.value}")
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
