"""Append-only audit log. Payloads hold ids, labels, numbers and hashes only, never message text."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

ALLOWED_PAYLOAD_KEYS = {
    "class", "subtype", "confidence", "rule_id", "template_id", "draft_hash", "old_hash", "new_hash",
    "reason_code", "field", "lang", "error_type", "state", "team", "ticket_id", "old_class", "new_class", "provider",
    # Which pattern of the rule fired, as its position in pack/rules.yaml (resolve it with the row's config
    # version). An index, not the keyword: a keyword that fired also occurs in the customer's message.
    "pattern_index",
}


class AuditEvent(BaseModel):
    id: int
    case_id: str
    ts: str
    actor: str
    event: str
    config_version: str
    payload: dict[str, Any]


class AuditLog:
    def __init__(self, conn: sqlite3.Connection, config_version: str):
        self.conn = conn
        self.config_version = config_version
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, ts TEXT NOT NULL,"
            " actor TEXT NOT NULL, event TEXT NOT NULL, config_version TEXT NOT NULL, payload_json TEXT NOT NULL)"
        )
        conn.commit()

    def write(self, case_id: str, actor: str, event: str, **payload: Any) -> None:
        unknown = set(payload) - ALLOWED_PAYLOAD_KEYS
        if unknown:
            raise ValueError(f"audit payload keys not allowed: {sorted(unknown)}")
        for value in payload.values():
            if not (value is None or isinstance(value, (int, float, bool)) or (isinstance(value, str) and len(value) <= 80)):
                raise ValueError("audit payload values must be short scalars")
        self.conn.execute(
            "INSERT INTO audit (case_id, ts, actor, event, config_version, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                case_id,
                datetime.now(timezone.utc).isoformat(),
                actor,
                event,
                self.config_version,
                json.dumps(payload, sort_keys=True),
            ),
        )
        self.conn.commit()

    def events(self, case_id: str | None = None) -> list[AuditEvent]:
        query = "SELECT id, case_id, ts, actor, event, config_version, payload_json FROM audit"
        args: tuple = ()
        if case_id is not None:
            query += " WHERE case_id = ?"
            args = (case_id,)
        rows = self.conn.execute(query + " ORDER BY id", args).fetchall()
        return [
            AuditEvent(id=r[0], case_id=r[1], ts=r[2], actor=r[3], event=r[4], config_version=r[5], payload=json.loads(r[6]))
            for r in rows
        ]
