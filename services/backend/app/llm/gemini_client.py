"""Google Gemini-backed :class:`LLMClient` (backend-contracts.md section 8).

Uses ``httpx.AsyncClient`` against the Gemini REST API
(``https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``).
The API key is always sent in the ``x-goog-api-key`` header and never in the
request URL so it cannot leak via URL logging or exception messages.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx

from app.llm.client import (
    LLMInvalidResponseError,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMResponse,
    LLMToolCall,
    LLMToolSpec,
    LLMUnavailableError,
)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_REFUSAL_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "IMAGE_SAFETY",
    }
)

_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$schema",
        "additionalProperties",
        "default",
        "examples",
        "title",
    }
)


def _retry_after_seconds(headers: httpx.Headers | None) -> int | None:
    if headers is None:
        return None
    header = headers.get("retry-after")
    if header is None:
        return None
    try:
        return int(float(header))
    except (TypeError, ValueError):
        return None


def _to_gemini_schema(
    schema: dict[str, Any], defs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Convert a Pydantic JSON Schema into a Gemini-compatible OpenAPI schema.

    Resolves ``$defs``/``$ref`` inline, converts ``anyOf: [T, {"type": "null"}]``
    into ``{...T, "nullable": True}``, and strips keywords rejected by the
    Gemini ``FunctionDeclaration.parameters`` validator.
    """
    if defs is None:
        raw_defs = schema.get("$defs")
        defs = raw_defs if isinstance(raw_defs, dict) else {}

    if "$ref" in schema:
        ref = str(schema["$ref"])
        prefix = "#/$defs/"
        if ref.startswith(prefix):
            target_name = ref[len(prefix) :]
            target = defs.get(target_name)
            if isinstance(target, dict):
                return _to_gemini_schema(copy.deepcopy(target), defs)
        return {"type": "object"}

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        non_null = [
            branch
            for branch in schema["anyOf"]
            if isinstance(branch, dict) and branch.get("type") != "null"
        ]
        has_null = any(
            isinstance(branch, dict) and branch.get("type") == "null"
            for branch in schema["anyOf"]
        )
        if len(non_null) == 1:
            merged = _to_gemini_schema(copy.deepcopy(non_null[0]), defs)
            if has_null:
                merged["nullable"] = True
            for key in ("description", "minLength", "maxLength", "minimum", "maximum"):
                if key in schema and key not in merged:
                    merged[key] = schema[key]
            return merged

    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS or key == "anyOf":
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned["properties"] = {
                prop_name: _to_gemini_schema(prop_schema, defs)
                for prop_name, prop_schema in value.items()
                if isinstance(prop_schema, dict)
            }
        elif key == "items" and isinstance(value, dict):
            cleaned["items"] = _to_gemini_schema(value, defs)
        else:
            cleaned[key] = value

    if "type" not in cleaned and "properties" in cleaned:
        cleaned["type"] = "object"
    return cleaned


def _collect_tool_names(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Map ``tool_use.id -> tool_use.name`` across earlier assistant messages."""
    mapping: dict[str, str] = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and isinstance(block.get("id"), str)
                and isinstance(block.get("name"), str)
            ):
                mapping[block["id"]] = block["name"]
    return mapping


def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_names = _collect_tool_names(messages)
    contents: list[dict[str, Any]] = []

    for message in messages:
        raw_role = str(message.get("role", "user"))
        role = "model" if raw_role == "assistant" else "user"
        content = message.get("content")
        parts: list[dict[str, Any]] = []

        if isinstance(content, str):
            parts.append({"text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    part: dict[str, Any] = {"text": str(block.get("text", ""))}
                    if block.get("thought") is True:
                        part["thought"] = True
                    if "thoughtSignature" in block:
                        part["thoughtSignature"] = block["thoughtSignature"]
                    parts.append(part)
                elif btype == "tool_use":
                    fc_part: dict[str, Any] = {
                        "functionCall": {
                            "name": str(block.get("name", "")),
                            "args": dict(block.get("input") or {}),
                        }
                    }
                    if "thoughtSignature" in block:
                        fc_part["thoughtSignature"] = block["thoughtSignature"]
                    parts.append(fc_part)
                elif btype == "tool_result":
                    tool_use_id = str(block.get("tool_use_id", ""))
                    fn_name = str(
                        block.get("name") or tool_names.get(tool_use_id) or tool_use_id
                    )
                    parts.append(
                        {
                            "functionResponse": {
                                "name": fn_name,
                                "response": {
                                    "result": str(block.get("content", "")),
                                    "is_error": bool(block.get("is_error", False)),
                                },
                            }
                        }
                    )

        if parts:
            contents.append({"role": role, "parts": parts})

    return contents


class GeminiLLMClient:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.5-flash",
        base_url: str = GEMINI_API_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[LLMToolSpec],
        max_tokens: int,
        timeout_seconds: float,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "contents": _convert_messages(messages),
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": _to_gemini_schema(tool.input_schema),
                        }
                        for tool in tools
                    ]
                }
            ]

        url = f"{self._base_url}/models/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

        try:
            if self._client is not None:
                http_response = await self._client.post(
                    url, json=payload, headers=headers, timeout=timeout_seconds
                )
            else:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    http_response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("gemini.TimeoutError") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("gemini.NetworkError") from exc

        status = http_response.status_code
        if status == 429:
            raise LLMRateLimitedError(
                "gemini.RateLimitError",
                retry_after_seconds=_retry_after_seconds(http_response.headers),
            )
        if status >= 500:
            raise LLMUnavailableError(f"gemini.HTTP_{status}")
        if status >= 400:
            raise LLMInvalidResponseError(f"gemini.HTTP_{status}")

        try:
            body = http_response.json()
        except ValueError as exc:
            raise LLMInvalidResponseError("gemini.MalformedJSON") from exc

        if not isinstance(body, dict):
            raise LLMInvalidResponseError("gemini.UnexpectedPayload")

        prompt_feedback = body.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            reason = prompt_feedback.get("blockReason")
            raise LLMRefusalError(f"refused: blockReason={reason!r}")

        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise LLMInvalidResponseError("gemini.NoCandidates")

        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        finish_reason = str(candidate.get("finishReason") or "STOP")

        if finish_reason in _REFUSAL_FINISH_REASONS:
            raise LLMRefusalError(f"refused: finishReason={finish_reason!r}")
        if finish_reason == "MAX_TOKENS":
            raise LLMInvalidResponseError("truncated")
        if finish_reason == "MALFORMED_FUNCTION_CALL":
            raise LLMInvalidResponseError("malformed_function_call")

        content_obj = candidate.get("content")
        raw_parts = content_obj.get("parts") if isinstance(content_obj, dict) else []
        if not isinstance(raw_parts, list):
            raw_parts = []

        text_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        raw_content: list[dict[str, Any]] = []

        for idx, part in enumerate(raw_parts):
            if not isinstance(part, dict):
                continue
            is_thought = part.get("thought") is True
            thought_sig = part.get("thoughtSignature")

            if "functionCall" in part and isinstance(part["functionCall"], dict):
                fc = part["functionCall"]
                fn_name = str(fc.get("name") or "")
                raw_args = fc.get("args")
                args = dict(raw_args) if isinstance(raw_args, dict) else {}
                call_id = str(fc.get("id") or f"gemini-{fn_name}-{idx + 1}")
                tool_calls.append(LLMToolCall(id=call_id, name=fn_name, arguments=args))
                block_dict: dict[str, Any] = {
                    "type": "tool_use",
                    "id": call_id,
                    "name": fn_name,
                    "input": args,
                }
                if thought_sig is not None:
                    block_dict["thoughtSignature"] = thought_sig
                raw_content.append(block_dict)
            elif "text" in part and isinstance(part["text"], str):
                if not is_thought:
                    text_parts.append(part["text"])
                text_block: dict[str, Any] = {"type": "text", "text": part["text"]}
                if is_thought:
                    text_block["thought"] = True
                if thought_sig is not None:
                    text_block["thoughtSignature"] = thought_sig
                raw_content.append(text_block)

        usage = body.get("usageMetadata")
        input_tokens = (
            int(usage.get("promptTokenCount", 0)) if isinstance(usage, dict) else 0
        )
        output_tokens = (
            int(usage.get("candidatesTokenCount", 0)) if isinstance(usage, dict) else 0
        )

        stop_reason = "tool_use" if tool_calls else finish_reason.lower()
        request_id = (
            http_response.headers.get("x-request-id")
            or http_response.headers.get("x-goog-request-id")
            or body.get("responseId")
        )

        return LLMResponse(
            text="".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider,
            model=self.model,
            request_id=str(request_id) if request_id is not None else None,
            raw_content=raw_content,
        )
