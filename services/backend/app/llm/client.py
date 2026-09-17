"""LLM boundary types and errors shared by every provider adapter.

See ``docs/architecture/backend-contracts.md`` section 8. These types are the
binding interface between ``app/llm`` and the agent framework (Task 12):
providers never leak SDK-specific types past ``LLMClient.complete``.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class LLMToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class LLMToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    text: str | None
    tool_calls: list[LLMToolCall]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    provider: str
    model: str
    request_id: str | None
    # The full content-block list of the assistant turn, verbatim (including
    # thinking blocks), so the agent loop can append it unmodified on the next
    # turn. Anthropic requires thinking blocks to be replayed unchanged; other
    # providers (fixture) never populate this.
    raw_content: list[dict[str, Any]] | None = None


class LLMClient(Protocol):
    provider: str  # "anthropic" | "fixture"
    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[LLMToolSpec],
        max_tokens: int,
        timeout_seconds: float,
    ) -> LLMResponse: ...


class LLMError(Exception):
    """Base class for every LLM boundary error.

    ``retryable`` tells callers (the agent loop, the orchestrator) whether
    re-issuing the same request could succeed.
    """

    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LLMUnavailableError(LLMError):
    """The provider could not be reached or failed transiently (network,

    timeout, 5xx). Retryable.
    """

    retryable = True


class LLMRateLimitedError(LLMError):
    """The provider rejected the request for rate limiting. Retryable."""

    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMRefusalError(LLMError):
    """The model declined to respond (safety refusal). Not retryable."""

    retryable = False


class LLMInvalidResponseError(LLMError):
    """The response could not be used (truncated, malformed, rejected request).

    Not retryable.
    """

    retryable = False


class LLMDisabledError(LLMError):
    """The provider is configured as ``disabled``. Not retryable."""

    retryable = False
