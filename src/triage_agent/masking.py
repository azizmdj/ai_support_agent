"""PII masking applied to every message before it reaches a real (non-fake) model.

Pattern based and deliberately over-masking: no checksum is required, so a mistyped IBAN or card
number is still masked, and some harmless numbers may be masked too. Detected:

- IBAN-shaped strings      -> <IBAN_1>, <IBAN_2>, ...
- 13-19 digit runs, optionally split by spaces or dashes (cards) -> <CARD_n>
- email addresses          -> <EMAIL_n>
- phone numbers starting with + or 0, at least 7 digits -> <PHONE_n>

NOT detected: names and free-text addresses. Masked text can still identify a person.

The mapping from placeholder to original value lives only in memory for one call; it is never
stored or logged. Models only classify, so the mapping is not needed to restore a draft;
unmask() exists for tests.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){2,7}[A-Z0-9]{1,4}\b", re.IGNORECASE)),
    ("CARD", re.compile(r"(?<![\d+])\d(?:[ -]?\d){12,18}(?!\d)")),  # not after "+": that is a phone
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+|0)\d[\d ()/-]{5,}\d(?!\d)")),
]


class MaskingError(Exception):
    """Masking failed. The caller must not send the text to a model (fail closed)."""


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace PII with numbered placeholders. Returns (masked_text, {placeholder: original})."""
    mapping: dict[str, str] = {}
    seen: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}

    for kind, pattern in _PATTERNS:
        def replace(match: re.Match[str], kind: str = kind) -> str:
            value = match.group(0)
            if kind == "PHONE" and sum(ch.isdigit() for ch in value) < 7:
                return value
            key = (kind, value)
            if key not in seen:
                counters[kind] = counters.get(kind, 0) + 1
                placeholder = f"<{kind}_{counters[kind]}>"
                seen[key] = placeholder
                mapping[placeholder] = value
            return seen[key]

        text = pattern.sub(replace, text)
    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text
