"""Text-model adapters. Each adapter only moves text over HTTP: no prompts, no parsing of labels."""

from __future__ import annotations

from typing import Protocol


class ProviderError(Exception):
    """Timeout, HTTP error or unusable response. Messages never contain keys or message text."""


class TextModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...


TIMEOUT_SECONDS = 20.0
