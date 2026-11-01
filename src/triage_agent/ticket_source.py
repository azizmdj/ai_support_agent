"""Ticket system interface plus an in-memory mock."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import Ticket


class TicketSource(Protocol):
    def fetch_new(self) -> list[Ticket]: ...
    def send_reply(self, ticket_id: str, body: str) -> None: ...


class MockTicketSource:
    """Serves tickets once each and records every reply it is asked to send."""

    def __init__(self, tickets: list[Ticket]):
        self._pending = list(tickets)
        self.sent: list[tuple[str, str]] = []

    def fetch_new(self) -> list[Ticket]:
        tickets, self._pending = self._pending, []
        return tickets

    def send_reply(self, ticket_id: str, body: str) -> None:
        self.sent.append((ticket_id, body))


def load_tickets(path: Path) -> list[Ticket]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [Ticket.model_validate(json.loads(line)) for line in lines if line.strip()]
