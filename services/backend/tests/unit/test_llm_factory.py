"""build_llm_client's five branches (backend-contracts.md section 8)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.factory import build_llm_client
from app.llm.fixture_client import FixtureLLMClient
from app.settings import Settings


def test_disabled_returns_none() -> None:
    settings = Settings(_env_file=None, environment="test", llm_provider="disabled")
    assert build_llm_client(settings) is None


def test_fixture_in_development_returns_fixture_client() -> None:
    settings = Settings(_env_file=None, environment="development", llm_provider="fixture")
    client = build_llm_client(settings)
    assert isinstance(client, FixtureLLMClient)


def test_fixture_in_test_environment_returns_fixture_client() -> None:
    settings = Settings(_env_file=None, environment="test", llm_provider="fixture")
    client = build_llm_client(settings)
    assert isinstance(client, FixtureLLMClient)


def test_fixture_in_production_raises() -> None:
    # `Settings` itself already refuses to construct with environment=
    # "production" and llm_provider="fixture" (its own model_validator,
    # defence in depth at settings-load time) -- see
    # test_settings.py::test_production_rejects_fixture_llm_provider.
    # `model_construct` bypasses that validator so this test exercises
    # `build_llm_client`'s own independent guard in isolation, in case the
    # two are ever called on a `Settings` object built a different way
    # (e.g. mutated after construction, or assembled by a future caller
    # that does not go through `Settings()`).
    settings = Settings.model_construct(environment="production", llm_provider="fixture")
    with pytest.raises(ValueError, match="fixture"):
        build_llm_client(settings)


def test_anthropic_without_key_raises() -> None:
    settings = Settings(_env_file=None, environment="test", llm_provider="anthropic")
    with pytest.raises(ValueError, match="anthropic_api_key"):
        build_llm_client(settings)


def test_anthropic_with_key_returns_configured_model() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        llm_provider="anthropic",
        anthropic_api_key=SecretStr("sk-ant-test-key-not-real"),
        anthropic_model="claude-opus-5",
    )
    client = build_llm_client(settings)
    assert isinstance(client, AnthropicLLMClient)
    assert client.model == "claude-opus-5"
    assert client.provider == "anthropic"
