"""Data models shared by every part of the agent."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum

from pydantic import AwareDatetime, BaseModel, Field, field_validator


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_hash(text: str) -> str:
    """Stable sha256 of a text, independent of CRLF/LF line endings."""
    return hashlib.sha256(normalize_newlines(text).encode("utf-8")).hexdigest()


class Ticket(BaseModel):
    """An incoming customer message as delivered by a TicketSource."""

    id: str
    received_at: AwareDatetime
    language: str
    text: str
    # Structured fields from the ticket system (never parsed from the message text).
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("received_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class ClassifierResult(BaseModel):
    label: str
    subtype: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    provider: str | None = None  # set by FallbackChain: which provider answered (never parsed from model output)


class CaseState(str, Enum):
    NEW = "new"
    DRAFTED = "drafted"
    NEEDS_PERSON = "needs_person"
    ESCALATED = "escalated"
    TAKEN = "taken"
    SENT = "sent"


ALLOWED_TRANSITIONS: dict[CaseState, set[CaseState]] = {
    CaseState.NEW: {CaseState.DRAFTED, CaseState.NEEDS_PERSON, CaseState.ESCALATED},
    CaseState.ESCALATED: {CaseState.TAKEN},
    CaseState.DRAFTED: {CaseState.SENT, CaseState.ESCALATED, CaseState.DRAFTED, CaseState.NEEDS_PERSON},
    CaseState.NEEDS_PERSON: {CaseState.ESCALATED, CaseState.DRAFTED, CaseState.NEEDS_PERSON},
}
# Derived from the table: a state with a way out is open (including `new`), a state without one is final.
OPEN_STATES: frozenset[CaseState] = frozenset(ALLOWED_TRANSITIONS)
FINAL_STATES: frozenset[CaseState] = frozenset(CaseState) - OPEN_STATES


class InvalidTransition(Exception):
    pass


class Case(BaseModel):
    id: str
    ticket_id: str
    language: str
    received_at: AwareDatetime
    config_version: str
    state: CaseState = CaseState.NEW
    class_name: str | None = None
    subtype: str | None = None
    confidence: float | None = None
    rule_id: str | None = None
    rule_pattern: str | None = None  # the config pattern that fired (from rules.yaml)
    predicted_class: str | None = None  # the model's label, kept even when low confidence sends it to "unclear"
    # The model's own short explanation, shown to the reviewer as unverified. Kept on the case only: it is
    # never written to the audit log or a log line and never used in a reply.
    model_explanation: str | None = None
    reason: str = ""
    team: str | None = None
    priority: str | None = None
    due_at: AwareDatetime | None = None
    template_id: str | None = None
    draft: str | None = None
    draft_hash: str | None = None
    edited: bool = False
    reviewer_id: str | None = None

    def transition(self, new_state: CaseState) -> None:
        if new_state not in ALLOWED_TRANSITIONS.get(self.state, set()):
            raise InvalidTransition(f"{self.id}: {self.state.value} -> {new_state.value} not allowed")
        self.state = new_state
