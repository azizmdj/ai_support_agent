"""Case storage: a CaseStore interface and the default SQLite implementation."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from .models import Case, Ticket


class CaseStore(Protocol):
    def save(self, case: Case) -> None: ...
    def get(self, case_id: str) -> Case: ...
    def list(self) -> list[Case]: ...
    def save_ticket(self, ticket: Ticket) -> None: ...
    def get_ticket(self, ticket_id: str) -> Ticket: ...
    def is_paused(self) -> bool: ...
    def set_paused(self, paused: bool) -> None: ...


class CaseNotFound(KeyError):
    pass


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    # The web server uses this connection from worker threads; callers serialize access with a lock.
    return sqlite3.connect(str(path), check_same_thread=False)


class SQLiteCaseStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cases ("
            " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, state TEXT NOT NULL, data_json TEXT NOT NULL)"
        )
        # The case store keeps the ticket (including message text) for the reviewer. The audit log never does.
        conn.execute("CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, data_json TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()

    def save(self, case: Case) -> None:
        self.conn.execute(
            "INSERT INTO cases (id, ticket_id, state, data_json) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET state = excluded.state, data_json = excluded.data_json",
            (case.id, case.ticket_id, case.state.value, case.model_dump_json()),
        )
        self.conn.commit()

    def get(self, case_id: str) -> Case:
        row = self.conn.execute("SELECT data_json FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise CaseNotFound(case_id)
        return Case.model_validate_json(row[0])

    def list(self) -> list[Case]:
        rows = self.conn.execute("SELECT data_json FROM cases ORDER BY rowid").fetchall()
        return [Case.model_validate_json(r[0]) for r in rows]

    def save_ticket(self, ticket: Ticket) -> None:
        self.conn.execute(
            "INSERT INTO tickets (id, data_json) VALUES (?, ?)"
            " ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json",
            (ticket.id, ticket.model_dump_json()),
        )
        self.conn.commit()

    def get_ticket(self, ticket_id: str) -> Ticket:
        row = self.conn.execute("SELECT data_json FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise CaseNotFound(ticket_id)
        return Ticket.model_validate_json(row[0])

    def is_paused(self) -> bool:
        row = self.conn.execute("SELECT value FROM flags WHERE name = 'paused'").fetchone()
        return row is not None and row[0] == "1"

    def set_paused(self, paused: bool) -> None:
        self.conn.execute(
            "INSERT INTO flags (name, value) VALUES ('paused', ?)"
            " ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            ("1" if paused else "0",),
        )
        self.conn.commit()
