import json

import httpx
import pytest

from triage_agent.providers import ProviderError
from triage_agent.providers.anthropic import AnthropicModel
from triage_agent.providers.openai_compatible import OpenAICompatibleModel

KEY = "test-key-not-real-0001"


def transport(handler, seen):
    def wrapped(request: httpx.Request):
        seen.append(request)
        return handler(request)
    return httpx.MockTransport(wrapped)


def ok_json(payload):
    return lambda request: httpx.Response(200, json=payload)


def raise_timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


# --- OpenAI-compatible -----------------------------------------------------------------------------
def openai(handler, seen, key=KEY):
    return OpenAICompatibleModel("https://api.example.test/v1/", "gpt-test", key, transport=transport(handler, seen))


def test_openai_request_shape_and_parsing():
    seen = []
    model = openai(ok_json({"choices": [{"message": {"role": "assistant", "content": "hello"}}]}), seen)
    assert model.complete("SYSTEM TEXT", "USER TEXT") == "hello"
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.example.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "gpt-test"
    assert body["temperature"] == 0
    assert 0 < body["max_tokens"] <= 500
    assert body["messages"] == [{"role": "system", "content": "SYSTEM TEXT"}, {"role": "user", "content": "USER TEXT"}]


def test_openai_without_key_sends_no_auth_header():
    seen = []
    model = openai(ok_json({"choices": [{"message": {"content": "x"}}]}), seen, key=None)
    model.complete("s", "u")
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={"error": {"message": "boom"}}),
    lambda r: httpx.Response(401, text="bad key " + KEY),
    raise_timeout,
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"choices": []}),
    lambda r: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
    lambda r: httpx.Response(200, json={"unexpected": True}),
])
def test_openai_errors_raise_provider_error(handler):
    with pytest.raises(ProviderError) as info:
        openai(handler, []).complete("s", "u")
    assert KEY not in str(info.value)


# --- Anthropic -----------------------------------------------------------------------------------
def anthropic(handler, seen):
    return AnthropicModel("https://api.anthropic.example/", "claude-test", KEY, transport=transport(handler, seen))


def test_anthropic_request_shape_and_parsing():
    seen = []
    reply = {
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"label": "x"}'}],
        "stop_reason": "end_turn",
    }
    model = anthropic(ok_json(reply), seen)
    assert model.complete("SYSTEM TEXT", "USER TEXT") == '{"label": "x"}'
    request = seen[0]
    assert str(request.url) == "https://api.anthropic.example/v1/messages"
    assert request.headers["x-api-key"] == KEY
    assert request.headers["anthropic-version"] == "2026-10-21"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "claude-test"
    assert body["system"] == "SYSTEM TEXT"
    assert body["messages"] == [{"role": "user", "content": "USER TEXT"}]
    assert 0 < body["max_tokens"] <= 2048
    # API version 2026-10-21 rejects sampling parameters, so none are sent.
    assert not {"temperature", "top_p", "top_k"} & set(body)


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={"type": "error"}),
    lambda r: httpx.Response(529, text="overloaded"),
    raise_timeout,
    lambda r: httpx.Response(200, text="<html>"),
    lambda r: httpx.Response(200, json={"content": "nope"}),
    lambda r: httpx.Response(200, json={"content": [{"type": "thinking", "thinking": ""}], "stop_reason": "max_tokens"}),
    lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "stop_reason": "refusal"}),
])
def test_anthropic_errors_raise_provider_error(handler):
    with pytest.raises(ProviderError) as info:
        anthropic(handler, []).complete("s", "u")
    assert KEY not in str(info.value)
