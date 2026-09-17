"""LLM boundary: provider adapters, run budgets, and redaction.

See ``docs/architecture/backend-contracts.md`` section 8 and
``docs/architecture/llm-boundary.md``.
"""

from __future__ import annotations

from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.budget import record_usage, reserve_model_call, token_budget_remaining
from app.llm.client import (
    LLMClient,
    LLMDisabledError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMResponse,
    LLMToolCall,
    LLMToolSpec,
    LLMUnavailableError,
)
from app.llm.factory import build_llm_client
from app.llm.fixture_client import (
    FixtureLLMClient,
    FixtureRequest,
    FixtureScript,
    default_fixture_script,
)
from app.llm.redaction import redact_payload, redact_text

__all__ = [
    "AnthropicLLMClient",
    "FixtureLLMClient",
    "FixtureRequest",
    "FixtureScript",
    "LLMClient",
    "LLMDisabledError",
    "LLMError",
    "LLMInvalidResponseError",
    "LLMRateLimitedError",
    "LLMRefusalError",
    "LLMResponse",
    "LLMToolCall",
    "LLMToolSpec",
    "LLMUnavailableError",
    "build_llm_client",
    "default_fixture_script",
    "record_usage",
    "redact_payload",
    "redact_text",
    "reserve_model_call",
    "token_budget_remaining",
]
