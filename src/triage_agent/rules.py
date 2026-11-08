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


def matched_pattern(rule: Rule, ticket: Ticket, normalized_text: str) -> int | None:
    """Index of the first pattern of the rule that matches, or None."""
    if rule.languages is not None and ticket.language not in rule.languages:
        return None
    return next((i for i, p in enumerate(rule.patterns) if pattern_matches(p, normalized_text)), None)


def rule_matches(rule: Rule, ticket: Ticket, normalized_text: str) -> bool:
    return matched_pattern(rule, ticket, normalized_text) is not None


@dataclass(frozen=True)
class RuleHit:
    rule: Rule
    class_name: str
    pattern_index: int  # position in rule.patterns of the pattern that fired

    @property
    def pattern(self) -> str:
        """The pattern as written in pack/rules.yaml (config text, not the customer's text)."""
        return self.rule.patterns[self.pattern_index]


def match_rules(ticket: Ticket, config: Config) -> RuleHit | None:
    """Return the winning rule: highest class priority, then file order."""
    text = normalize(ticket.text)
    best: RuleHit | None = None
    for rule in config.rules:
        index = matched_pattern(rule, ticket, text)
        if index is None:
            continue
        hit = RuleHit(rule=rule, class_name=rule.force_class, pattern_index=index)
        if best is None or config.classes[hit.class_name].rank > config.classes[best.class_name].rank:
            best = hit
    return best
