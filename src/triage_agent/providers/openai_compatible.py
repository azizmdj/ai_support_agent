"""OpenAI-compatible chat completions: hosted APIs and local servers such as Ollama or vLLM."""

from __future__ import annotations

import httpx

from . import TIMEOUT_SECONDS, ProviderError


class OpenAICompatibleModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 300,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.max_tokens = max_tokens
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS, transport=transport)

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
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
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("malformed response") from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty response")
        return content
