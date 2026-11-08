"""Read-only facts for the Monitor page: case counts, overdue cases and the latest evaluation report.
Nothing here writes; the eval report is parsed from the Markdown that `eval` writes to eval/results/."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..models import Case, CaseState

OPEN_STATES = (CaseState.ESCALATED, CaseState.DRAFTED, CaseState.NEEDS_PERSON)
STATE_LABELS = [
    (CaseState.ESCALATED, "Urgent (escalated)"),
    (CaseState.DRAFTED, "Ready to approve"),
    (CaseState.NEEDS_PERSON, "Needs a person"),
    (CaseState.SENT, "Sent"),
    (CaseState.TAKEN, "Taken by a person"),
]

_TITLE = re.compile(r"^# Evaluation: (?P<provider>.+), split (?P<split>\w+), (?P<date>\d{4}-\d{2}-\d{2})\s*$", re.M)
_ROW = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<value>[^|]*?)\s*\|\s*$", re.M)


def state_counts(cases: list[Case]) -> list[tuple[str, int]]:
    counts = Counter(c.state for c in cases)
    return [(label, counts.get(state, 0)) for state, label in STATE_LABELS]


def overdue(cases: list[Case], now: datetime) -> list[Case]:
    """Open cases past their deadline, most overdue first."""
    late = [c for c in cases if c.state in OPEN_STATES and c.due_at is not None and c.due_at < now]
    return sorted(late, key=lambda c: c.due_at)


def class_counts(cases: list[Case]) -> list[dict]:
    rows: dict[str, dict] = {}
    for c in cases:
        row = rows.setdefault(c.class_name or "-", {"name": c.class_name or "-", "open": 0, "done": 0})
        row["open" if c.state in OPEN_STATES else "done"] += 1
    return sorted(rows.values(), key=lambda r: (-(r["open"] + r["done"]), r["name"]))


def parse_report(text: str) -> dict | None:
    """The header and the two-column tables of one report. None if it is not an eval report."""
    title = _TITLE.search(text)
    if not title:
        return None
    values = {m["key"]: m["value"] for m in _ROW.finditer(text)}
    return {**title.groupdict(), "values": values}


def latest_eval(results_dir: Path) -> dict | None:
    """The newest report by run date; on the same date the held-out test split wins, as the clean figure."""
    reports = []
    for path in sorted(results_dir.glob("*.md")) if results_dir.is_dir() else []:
        report = parse_report(path.read_text(encoding="utf-8"))
        if report:
            reports.append({**report, "file": f"eval/results/{path.name}"})
    if not reports:
        return None
    best = max(reports, key=lambda r: (r["date"], r["split"] == "test"))
    v = best["values"]

    def num(key: str) -> float | None:
        try:
            return float(v.get(key, ""))
        except ValueError:
            return None

    tickets, accuracy = num("tickets"), num("class_accuracy")
    pii, leak = num("pii_tickets"), num("pii_leak_rate")
    return {
        "provider": best["provider"],
        "split": best["split"],
        "date": best["date"],
        "file": best["file"],
        "config_version": v.get("config_version", "").strip("`") or None,
        "tickets": int(tickets) if tickets is not None else None,
        "risky_drafted": v.get("risky_drafted"),
        "pii_leaks": round(leak * pii) if leak is not None and pii is not None else None,
        "pii_tickets": int(pii) if pii is not None else None,
        "classes_correct": round(accuracy * tickets) if accuracy is not None and tickets is not None else None,
        "fake": best["provider"].split(",")[0].strip() == "fake",
    }
