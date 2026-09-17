"""Anthropic-backed :class:`LLMClient` (backend-contracts.md section 8).

Built on ``anthropic`` SDK 1.x (``httpx2``-based; never import the ``httpx``
package here). ``AsyncAnthropic`` is constructed once in ``__init__`` with
``max_retries`` (which governs the SDK's own retry policy for connection
errors and 408/409/429/5xx); the per-call ``timeout_seconds`` is applied with
``with_options(timeout=...)`` so each ``complete()`` call can use a different
deadline (e.g. a tighter budget near a run's overall deadline) without
mutating the shared client.
"""

from __future__ import annotations

from typing import Any, cast

import anthropic

from app.llm.client import (
    LLMInvalidResponseError,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMResponse,
    LLMToolCall,
    LLMToolSpec,
    LLMUnavailableError,
)

# Opus 5 has refusal-fallback enabled via a beta flag rather than by default,
# per current API guidance (see docs/architecture/llm-boundary.md); every
# other model id is called through the plain (non-beta) endpoint so we never
# send betas/fallbacks/adaptive-thinking parameters a model might reject.
OPUS_5_MODEL = "claude-opus-5"
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _retry_after_seconds(exc: anthropic.RateLimitError) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    header = headers.get("retry-after")
    if header is None:
        return None
    try:
        return int(float(header))
    except (TypeError, ValueError):
        return None


class AnthropicLLMClient:
    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_retries: int = 2,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries)

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[LLMToolSpec],
        max_tokens: int,
        timeout_seconds: float,
    ) -> LLMResponse:
        tool_dicts = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]
        scoped_client = self._client.with_options(timeout=timeout_seconds)
        # The SDK's `messages`/`tools` param types are large unions of typed
        # dicts describing every content-block/tool-type variant; our public
        # `complete()` signature (backend-contracts.md section 8) intentionally
        # keeps these as plain `dict[str, Any]` so the agent framework never
        # imports SDK types, so we cast at this single boundary instead.
        sdk_messages = cast(Any, messages)
        sdk_tools = cast(Any, tool_dicts)
        response: Any

        try:
            if self.model == OPUS_5_MODEL:
                response = await scoped_client.beta.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=sdk_messages,
                    tools=sdk_tools,
                    betas=[REFUSAL_FALLBACK_BETA],
                    fallbacks="default",
                    thinking={"type": "adaptive"},
                    output_config={"effort": "medium"},
                )
            else:
                response = await scoped_client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=sdk_messages,
                    tools=sdk_tools,
                )
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitedError(
                "anthropic.RateLimitError", retry_after_seconds=_retry_after_seconds(exc)
            ) from exc
        except anthropic.APITimeoutError as exc:
            # APITimeoutError subclasses APIConnectionError; catch it first.
            raise LLMUnavailableError("anthropic.APITimeoutError") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("anthropic.APIConnectionError") from exc
        except anthropic.InternalServerError as exc:
            raise LLMUnavailableError("anthropic.InternalServerError") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMInvalidResponseError("anthropic.AuthenticationError") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMInvalidResponseError("anthropic.PermissionDeniedError") from exc
        except anthropic.BadRequestError as exc:
            raise LLMInvalidResponseError("anthropic.BadRequestError") from exc
        except anthropic.NotFoundError as exc:
            raise LLMInvalidResponseError("anthropic.NotFoundError") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMUnavailableError("anthropic.APIStatusError") from exc
            raise LLMInvalidResponseError("anthropic.APIStatusError") from exc

        if response.stop_reason == "refusal":
            stop_details = getattr(response, "stop_details", None)
            category = getattr(stop_details, "category", None)
            explanation = getattr(stop_details, "explanation", None)
            raise LLMRefusalError(f"refused: category={category!r} explanation={explanation!r}")
        if response.stop_reason == "max_tokens":
            raise LLMInvalidResponseError("truncated")

        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        raw_content: list[dict[str, Any]] = []
        for block in response.content:
            raw_content.append(block.to_dict())
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # Arguments come from the SDK-parsed `input` dict, never from
                # string-matching the serialised JSON (models may vary escaping).
                tool_calls.append(
                    LLMToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
            # `thinking` blocks are intentionally not surfaced in `text`; they
            # are preserved verbatim in `raw_content` for replay.

        return LLMResponse(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            provider=self.provider,
            model=self.model,
            request_id=getattr(response, "_request_id", None),
            raw_content=raw_content,
        )
