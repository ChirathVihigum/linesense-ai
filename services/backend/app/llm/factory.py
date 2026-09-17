"""Build the configured :class:`LLMClient` from application settings."""

from __future__ import annotations

from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.client import LLMClient
from app.llm.fixture_client import FixtureLLMClient
from app.settings import Settings


def build_llm_client(settings: Settings) -> LLMClient | None:
    """Return the provider client for ``settings.llm_provider``, or ``None``.

    ``None`` means ``disabled``: callers must fall back to deterministic,
    model-free behaviour (backend-contracts.md section 8).
    """
    if settings.llm_provider == "disabled":
        return None

    if settings.llm_provider == "fixture":
        if settings.environment == "production":
            raise ValueError("llm_provider 'fixture' is not allowed when LS_ENVIRONMENT=production")
        return FixtureLLMClient()

    if settings.llm_provider == "anthropic":
        api_key = settings.anthropic_api_key.get_secret_value()
        if not api_key:
            raise ValueError("anthropic_api_key is required when llm_provider is 'anthropic'")
        return AnthropicLLMClient(api_key=api_key, model=settings.anthropic_model)

    raise ValueError(f"unknown llm_provider {settings.llm_provider!r}")
