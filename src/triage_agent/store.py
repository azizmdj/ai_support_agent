"""Case storage: a CaseStore interface and the default SQLite implementation."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from .models import Case


class CaseStore(Protocol):
    def save(self, case: Case) -> None: ...
    def get(self, case_id: str) -> Case: ...
    def list(self) -> list[Case]: ...


class CaseNotFound(KeyError):
    pass


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    return sqlite3.connect(str(path))


class SQLiteCaseStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cases ("
            " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, state TEXT NOT NULL, data_json TEXT NOT NULL)"
        )
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
