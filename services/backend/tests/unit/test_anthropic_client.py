"""AnthropicLLMClient against in-test stub SDK clients (no network calls).

Anthropic SDK errors are constructed with real ``httpx2`` request/response
objects (the SDK's own dependency, not the ``httpx`` package) because
``anthropic.APIStatusError.__init__`` reads ``response.status_code`` and
``response.request`` directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest

from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.client import (
    LLMInvalidResponseError,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMToolSpec,
    LLMUnavailableError,
)

API_KEY = "sk-ant-should-never-leak-into-errors"  # noqa: S105 (test fixture, not a real secret)


@dataclass
class FakeBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    thinking: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type}
        for key in ("text", "id", "name", "input", "thinking"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeStopDetails:
    category: str | None = None
    explanation: str | None = None


@dataclass
class FakeResponse:
    stop_reason: str
    content: list[FakeBlock] = field(default_factory=list)
    usage: FakeUsage = field(default_factory=lambda: FakeUsage(10, 5))
    stop_details: FakeStopDetails | None = None
    _request_id: str | None = "req-fake-1"


class _StubEndpoint:
    """Stands in for ``client.beta.messages`` or ``client.messages``."""

    def __init__(
        self, *, response: FakeResponse | None = None, exc: Exception | None = None
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


class _StubBeta:
    def __init__(self, messages: _StubEndpoint) -> None:
        self.messages = messages


class StubAsyncAnthropic:
    def __init__(
        self, *, response: FakeResponse | None = None, exc: Exception | None = None
    ) -> None:
        self.beta_messages = _StubEndpoint(response=response, exc=exc)
        self.plain_messages = _StubEndpoint(response=response, exc=exc)
        self.beta = _StubBeta(self.beta_messages)
        self.messages = self.plain_messages

    def with_options(self, **_kwargs: Any) -> StubAsyncAnthropic:
        return self


TOOL = LLMToolSpec(
    name="lookup_thing",
    description="Look up a thing",
    input_schema={"type": "object", "properties": {}},
)


def _run(client: AnthropicLLMClient) -> Any:
    return asyncio.run(
        client.complete(
            system="be helpful",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[TOOL],
            max_tokens=1024,
            timeout_seconds=30,
        )
    )


def _make_http_response(status_code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx2.Response(
        status_code,
        request=request,
        headers=headers or {},
        json={"type": "error", "error": {"type": "x", "message": "opaque"}},
    )


# --- tool_use / text / thinking conversion, opus-5 betas ------------------


def test_opus5_call_includes_betas_and_fallbacks_and_converts_tool_use() -> None:
    response = FakeResponse(
        stop_reason="tool_use",
        content=[
            FakeBlock(type="thinking", thinking="internal reasoning"),
            FakeBlock(type="text", text="Here is my answer: "),
            FakeBlock(type="tool_use", id="call-1", name="lookup_thing", input={"a": 1}),
        ],
        usage=FakeUsage(input_tokens=42, output_tokens=17),
    )
    stub = StubAsyncAnthropic(response=response)
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-opus-5", client=stub)

    result = _run(client)

    assert stub.beta_messages.calls, "expected the beta endpoint to be used"
    call_kwargs = stub.beta_messages.calls[0]
    assert call_kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert call_kwargs["fallbacks"] == "default"
    assert call_kwargs["thinking"] == {"type": "adaptive"}
    assert call_kwargs["output_config"] == {"effort": "medium"}
    assert not stub.plain_messages.calls

    assert result.text == "Here is my answer: "
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "lookup_thing"
    assert result.tool_calls[0].arguments == {"a": 1}
    assert result.stop_reason == "tool_use"
    assert result.input_tokens == 42
    assert result.output_tokens == 17
    assert result.provider == "anthropic"
    assert result.model == "claude-opus-5"
    assert result.request_id == "req-fake-1"
    # Thinking blocks are preserved verbatim for replay, but excluded from `text`.
    assert result.raw_content is not None
    assert result.raw_content[0]["type"] == "thinking"
    assert len(result.raw_content) == 3


def test_non_opus_model_omits_betas_and_fallbacks() -> None:
    response = FakeResponse(
        stop_reason="end_turn",
        content=[FakeBlock(type="text", text="hello")],
    )
    stub = StubAsyncAnthropic(response=response)
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-sonnet-5", client=stub)

    _run(client)

    assert stub.plain_messages.calls, "expected the plain endpoint to be used"
    call_kwargs = stub.plain_messages.calls[0]
    assert "betas" not in call_kwargs
    assert "fallbacks" not in call_kwargs
    assert "thinking" not in call_kwargs
    assert "output_config" not in call_kwargs
    assert not stub.beta_messages.calls


# --- stop_reason handling ---------------------------------------------------


def test_refusal_stop_reason_raises_refusal_error() -> None:
    response = FakeResponse(
        stop_reason="refusal",
        stop_details=FakeStopDetails(category="cyber", explanation="policy"),
    )
    stub = StubAsyncAnthropic(response=response)
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-opus-5", client=stub)

    with pytest.raises(LLMRefusalError) as excinfo:
        _run(client)
    assert excinfo.value.retryable is False
    assert "cyber" in str(excinfo.value)


def test_max_tokens_stop_reason_raises_invalid_response() -> None:
    response = FakeResponse(
        stop_reason="max_tokens", content=[FakeBlock(type="text", text="cut off")]
    )
    stub = StubAsyncAnthropic(response=response)
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-opus-5", client=stub)

    with pytest.raises(LLMInvalidResponseError, match="truncated"):
        _run(client)


# --- error mapping -----------------------------------------------------------


@pytest.mark.parametrize(
    ("build_exc", "expected_type", "expected_retryable"),
    [
        (
            lambda: anthropic.RateLimitError(
                "rate limited",
                response=_make_http_response(429, headers={"retry-after": "42"}),
                body=None,
            ),
            LLMRateLimitedError,
            True,
        ),
        (
            lambda: anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")),
            LLMUnavailableError,
            True,
        ),
        (
            lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            LLMUnavailableError,
            True,
        ),
        (
            lambda: anthropic.InternalServerError(
                "server error", response=_make_http_response(500), body=None
            ),
            LLMUnavailableError,
            True,
        ),
        (
            lambda: anthropic.AuthenticationError(
                "bad key", response=_make_http_response(401), body=None
            ),
            LLMInvalidResponseError,
            False,
        ),
        (
            lambda: anthropic.PermissionDeniedError(
                "denied", response=_make_http_response(403), body=None
            ),
            LLMInvalidResponseError,
            False,
        ),
        (
            lambda: anthropic.BadRequestError(
                "bad request", response=_make_http_response(400), body=None
            ),
            LLMInvalidResponseError,
            False,
        ),
        (
            lambda: anthropic.NotFoundError(
                "not found", response=_make_http_response(404), body=None
            ),
            LLMInvalidResponseError,
            False,
        ),
        (
            lambda: anthropic.OverloadedError(
                "overloaded", response=_make_http_response(529), body=None
            ),
            LLMUnavailableError,
            True,
        ),
        (
            lambda: anthropic.ConflictError(
                "conflict", response=_make_http_response(409), body=None
            ),
            LLMInvalidResponseError,
            False,
        ),
    ],
)
def test_sdk_errors_map_to_typed_llm_errors_without_leaking_the_api_key(
    build_exc: Any, expected_type: type, expected_retryable: bool
) -> None:
    stub = StubAsyncAnthropic(exc=build_exc())
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-opus-5", client=stub)

    with pytest.raises(expected_type) as excinfo:
        _run(client)

    assert excinfo.value.retryable is expected_retryable
    assert API_KEY not in str(excinfo.value)


def test_rate_limit_error_carries_retry_after_seconds() -> None:
    exc = anthropic.RateLimitError(
        "rate limited", response=_make_http_response(429, headers={"retry-after": "17"}), body=None
    )
    stub = StubAsyncAnthropic(exc=exc)
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-opus-5", client=stub)

    with pytest.raises(LLMRateLimitedError) as excinfo:
        _run(client)
    assert excinfo.value.retry_after_seconds == 17


def test_client_constructed_without_injected_stub_uses_real_sdk_client() -> None:
    client = AnthropicLLMClient(api_key=API_KEY, model="claude-opus-5")
    assert isinstance(client._client, anthropic.AsyncAnthropic)  # noqa: SLF001
