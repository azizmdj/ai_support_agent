"""Anthropic Messages API over plain HTTP (POST /v1/messages)."""

from __future__ import annotations

import httpx

from . import TIMEOUT_SECONDS, ProviderError

API_VERSION = "2026-10-21"


class AnthropicModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = 1024,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.model = model
        # Current models always think; the budget leaves room for thinking plus a short JSON answer.
        self.max_tokens = max_tokens
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        self._client = httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS, transport=transport)

    def complete(self, system: str, user: str) -> str:
        # No temperature: API version 2026-10-21 rejects sampling parameters. Low effort keeps
        # thinking short for a classification task.
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "output_config": {"effort": "low"},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            response = self._client.post(self.url, json=body)
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection error ({type(exc).__name__})") from None
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code}")
        try:
            data = response.json()
            blocks = data["content"]
            stop_reason = data.get("stop_reason")
            text = "".join(b["text"] for b in blocks if b.get("type") == "text")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("malformed response") from None
        if stop_reason == "refusal":
            raise ProviderError("refusal")
        if not text.strip():
            raise ProviderError("empty response")
        return text
