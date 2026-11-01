"""Support triage agent: rules, classification, approved templates and a human approval gate."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACK = REPO_ROOT / "pack"
DEFAULT_TICKETS = REPO_ROOT / "tests" / "fixtures" / "tickets.jsonl"
