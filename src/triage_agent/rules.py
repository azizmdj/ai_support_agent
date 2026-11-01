"""Deterministic keyword/regex rules. They run before the LLM and always win."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .config import Config, Rule
from .models import Ticket


def normalize(text: str) -> str:
    """Casefold (ß -> ss), strip accents (ü -> u) and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def pattern_matches(pattern: str, text: str) -> bool:
    if pattern.startswith("re:"):
        return re.search(pattern[3:], text) is not None
    keyword = normalize(pattern)
    return re.search(r"(?<!\w)" + re.escape(keyword), text) is not None


def rule_matches(rule: Rule, ticket: Ticket, normalized_text: str) -> bool:
    if rule.languages is not None and ticket.language not in rule.languages:
        return False
    return any(pattern_matches(p, normalized_text) for p in rule.patterns)


@dataclass(frozen=True)
class RuleHit:
    rule: Rule
    class_name: str


def match_rules(ticket: Ticket, config: Config) -> RuleHit | None:
    """Return the winning rule: highest class priority, then file order."""
    text = normalize(ticket.text)
    best: RuleHit | None = None
    for rule in config.rules:
        if not rule_matches(rule, ticket, text):
            continue
        hit = RuleHit(rule=rule, class_name=rule.force_class)
        if best is None or config.classes[hit.class_name].rank > config.classes[best.class_name].rank:
            best = hit
    return best
