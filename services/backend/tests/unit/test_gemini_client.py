"""GeminiLLMClient unit tests against in-memory httpx MockTransport (no network calls)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.agents.base import SubmitAssessment
from app.llm.client import (
    LLMInvalidResponseError,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMToolSpec,
    LLMUnavailableError,
)
from app.llm.gemini_client import GeminiLLMClient

API_KEY = "gemini-test-key-should-never-leak"  # noqa: S105

TOOL = LLMToolSpec(
    name="lookup_thing",
    description="Look up a thing",
    input_schema={"type": "object", "properties": {"code": {"type": "string"}}},
)


def _client_with_handler(handler: Any, *, model: str = "gemini-2.5-flash") -> GeminiLLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return GeminiLLMClient(api_key=API_KEY, model=model, client=http_client)


def test_gemini_converts_tools_and_function_calls_and_preserves_thought_signature() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == API_KEY
        assert API_KEY not in str(request.url)
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            headers={"x-goog-request-id": "req-gemini-1"},
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "internal thought", "thought": True},
                                {"text": "Checking item: "},
                                {
                                    "functionCall": {
                                        "name": "lookup_thing",
                                        "args": {"code": "RM-01"},
                                    },
                                    "thoughtSignature": "sig-abc",
                                },
                            ],
                        },
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 55,
                    "candidatesTokenCount": 19,
                },
            },
        )

    client = _client_with_handler(handler)
    submit_spec = LLMToolSpec(
        name="submit_assessment",
        description="Submit final assessment",
        input_schema=SubmitAssessment.model_json_schema(),
    )

    result = asyncio.run(
        client.complete(
            system="be helpful",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[TOOL, submit_spec],
            max_tokens=1024,
            timeout_seconds=30.0,
        )
    )

    assert len(captured) == 1
    sent = captured[0]
    assert sent["systemInstruction"] == {"parts": [{"text": "be helpful"}]}
    decls = sent["tools"][0]["functionDeclarations"]
    assert len(decls) == 2
    # Verify $defs/$ref in SubmitAssessment were resolved inline for Gemini
    submit_params = decls[1]["parameters"]
    assert "$defs" not in json.dumps(submit_params)
    assert "$ref" not in json.dumps(submit_params)

    assert result.text == "Checking item: "
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "lookup_thing"
    assert result.tool_calls[0].arguments == {"code": "RM-01"}
    assert result.stop_reason == "tool_use"
    assert result.input_tokens == 55
    assert result.output_tokens == 19
    assert result.provider == "gemini"
    assert result.model == "gemini-2.5-flash"
    assert result.request_id == "req-gemini-1"
    assert result.raw_content is not None
    assert result.raw_content[2]["thoughtSignature"] == "sig-abc"


def test_gemini_replays_tool_use_and_tool_result_with_resolved_function_name() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"role": "model", "parts": [{"text": "done"}]},
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
            },
        )

    client = _client_with_handler(handler)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": "start"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-42",
                    "name": "lookup_thing",
                    "input": {"code": "A"},
                    "thoughtSignature": "sig-1",
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-42", "content": '{"ok": true}'}
            ],
        },
    ]

    result = asyncio.run(
        client.complete(
            system="sys",
            messages=messages,
            tools=[TOOL],
            max_tokens=512,
            timeout_seconds=15.0,
        )
    )

    assert result.text == "done"
    assert result.stop_reason == "stop"
    contents = captured[0]["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "lookup_thing"
    assert contents[1]["parts"][0]["thoughtSignature"] == "sig-1"
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "lookup_thing"


def test_safety_finish_reason_raises_refusal_error() -> None:
    client = _client_with_handler(
        lambda _req: httpx.Response(
            200,
            json={"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
        )
    )
    with pytest.raises(LLMRefusalError):
        asyncio.run(
            client.complete(
                system="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                max_tokens=64,
                timeout_seconds=5,
            )
        )


def test_max_tokens_finish_reason_raises_invalid_response() -> None:
    client = _client_with_handler(
        lambda _req: httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "cut"}]}}
                ]
            },
        )
    )
    with pytest.raises(LLMInvalidResponseError, match="truncated"):
        asyncio.run(
            client.complete(
                system="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                max_tokens=64,
                timeout_seconds=5,
            )
        )


@pytest.mark.parametrize(
    ("status_code", "headers", "expected_type", "expected_retryable"),
    [
        (429, {"retry-after": "12"}, LLMRateLimitedError, True),
        (500, {}, LLMUnavailableError, True),
        (503, {}, LLMUnavailableError, True),
        (400, {}, LLMInvalidResponseError, False),
        (401, {}, LLMInvalidResponseError, False),
        (403, {}, LLMInvalidResponseError, False),
    ],
)
def test_http_errors_map_to_typed_llm_errors_without_leaking_key(
    status_code: int,
    headers: dict[str, str],
    expected_type: type[Exception],
    expected_retryable: bool,
) -> None:
    client = _client_with_handler(
        lambda _req: httpx.Response(
            status_code, headers=headers, json={"error": {"message": "err"}}
        )
    )
    with pytest.raises(expected_type) as excinfo:
        asyncio.run(
            client.complete(
                system="s",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                max_tokens=64,
                timeout_seconds=5,
            )
        )
    assert getattr(excinfo.value, "retryable", None) is expected_retryable
    assert API_KEY not in str(excinfo.value)
