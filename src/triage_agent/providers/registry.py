"""Build the active provider chain from models.yaml (or TRIAGE_PROVIDER) without code changes."""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from ..config import Config, ConfigError, ProviderConfig
from ..llm import FakeLLM, FallbackChain, LLMClassifier, LLMProvider
from .anthropic import AnthropicModel
from .openai_compatible import OpenAICompatibleModel

ENV_OVERRIDE = "TRIAGE_PROVIDER"


def active_route(config: Config, env: Mapping[str, str]) -> list[str]:
    override = env.get(ENV_OVERRIDE, "").strip()
    route = [p.strip() for p in override.split(",") if p.strip()] if override else config.models.default_route
    unknown = [pid for pid in route if pid not in config.models.providers]
    if unknown:
        source = ENV_OVERRIDE if override else "routing.default"
        raise ConfigError([f"{source}: unknown provider id(s) {unknown}"])
    return route


def _key(provider: ProviderConfig, env: Mapping[str, str]) -> str | None:
    if not provider.api_key_env:
        return None
    value = env.get(provider.api_key_env, "")
    if not value.strip():
        # Name the variable, never a value.
        raise ConfigError([
            f"provider {provider.id} needs the environment variable {provider.api_key_env}, which is not set"
        ])
    return value


def build_provider(
    provider: ProviderConfig, env: Mapping[str, str], transport: httpx.BaseTransport | None = None
) -> LLMProvider:
    if provider.kind == "fake":
        return FakeLLM()
    key = _key(provider, env)
    if provider.kind == "openai_compatible":
        return LLMClassifier(OpenAICompatibleModel(provider.base_url, provider.model, key, transport=transport))
    if key is None:
        raise ConfigError([f"provider {provider.id}: kind anthropic needs api_key_env"])
    return LLMClassifier(AnthropicModel(provider.base_url, provider.model, key, transport=transport))


def build_llm(
    config: Config, env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None
) -> FallbackChain:
    env = os.environ if env is None else env
    route = active_route(config, env)
    return FallbackChain([(pid, build_provider(config.models.providers[pid], env, transport)) for pid in route])
