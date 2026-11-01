"""LLM provider interface plus a deterministic fake for tests and the offline demo."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from .config import ClassConfig
from .models import ClassifierResult
from .rules import pattern_matches, normalize


class LLMProvider(Protocol):
    def classify(self, text: str, classes: Sequence[ClassConfig]) -> ClassifierResult: ...


# (label, subtype, keywords). Subtype entries come first so they win inside a class.
DEFAULT_KEYWORDS: list[tuple[str, str | None, list[str]]] = [
    ("card_blocked", "lost_or_stolen", ["lost", "stolen", "verloren", "gestohlen"]),
    ("card_blocked", "blocked_after_pin_attempts", ["pin"]),
    ("card_blocked", None, ["blocked", "gesperrt", "card declined", "karte abgelehnt"]),
    ("pending_transfer", None, ["transfer", "uberweisung", "pending", "ausstehend", "sepa"]),
    ("fee_question", None, ["fee", "gebuhr", "kosten", "charged"]),
    ("complaint", None, ["unhappy", "unzufrieden", "terrible", "unacceptable", "inakzeptabel"]),
    ("general_question", None, ["invoice", "rechnung", "statement", "kontoauszug", "how do i", "wie kann ich"]),
]


class FakeLLM:
    """Keyword classifier. With `script`, returns (or raises) the scripted items in order instead.

    Scripted items may be ClassifierResult, a raw dict/object (to test invalid output) or an Exception.
    """

    def __init__(
        self,
        script: Iterable[Any] | None = None,
        keywords: list[tuple[str, str | None, list[str]]] | None = None,
    ):
        self._script = list(script) if script is not None else None
        self.keywords = keywords if keywords is not None else DEFAULT_KEYWORDS
        self.calls = 0

    def classify(self, text: str, classes: Sequence[ClassConfig]) -> Any:
        self.calls += 1
        if self._script is not None:
            item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
            if isinstance(item, BaseException):
                raise item
            return item
        known = {c.name for c in classes}
        normalized = normalize(text)
        hits: dict[str, str | None] = {}
        for label, subtype, words in self.keywords:
            if label in known and any(pattern_matches(w, normalized) for w in words):
                hits.setdefault(label, subtype)
        if not hits:
            return ClassifierResult(label="unclear", confidence=0.2, reason="no known topic")
        label, subtype = next(iter(hits.items()))
        if len(hits) > 1:
            return ClassifierResult(
                label=label, subtype=subtype, confidence=0.5, reason="several topics: " + ", ".join(hits)
            )
        return ClassifierResult(label=label, subtype=subtype, confidence=0.9, reason="keyword match")
