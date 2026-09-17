"""Deterministic, network-free :class:`LLMClient` used for tests/CI.

``LLM_PROVIDER=fixture`` (backend-contracts.md section 8): every response is
labelled ``provider="fixture"`` and produced by a pure function of the
request, so the same conversation always yields the same tool call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.llm.client import LLMResponse, LLMToolCall, LLMToolSpec

SUBMIT_TOOL_NAME = "submit_assessment"
_CONTEXT_RE = re.compile(r"<context>(.*?)</context>", re.DOTALL)


@dataclass(frozen=True)
class FixtureRequest:
    system: str
    messages: list[dict[str, Any]]
    tools: list[LLMToolSpec]


FixtureScript = Callable[[FixtureRequest], LLMResponse]


def _content_blocks(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    blocks: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    blocks.append((str(message.get("role", "")), block))
    return blocks


def _tool_result_count(messages: list[dict[str, Any]]) -> int:
    return sum(1 for _, block in _content_blocks(messages) if block.get("type") == "tool_result")


def _called_tool_names(messages: list[dict[str, Any]]) -> set[str]:
    return {
        block["name"]
        for _, block in _content_blocks(messages)
        if block.get("type") == "tool_use" and isinstance(block.get("name"), str)
    }


def _first_user_text_blocks(messages: list[dict[str, Any]]) -> list[str]:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            return [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
        return []
    return []


def _extract_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse the JSON object between ``<context>``/``</context>`` in the first
    user message, or ``{}`` when absent/unparsable."""
    for text in _first_user_text_blocks(messages):
        match = _CONTEXT_RE.search(text)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _arguments_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build tool-call arguments from a JSON schema's per-property defaults.

    Investigative tool schemas (Task 12) provide either ``default`` or
    ``examples`` for every property; a property with neither is omitted.
    """
    arguments: dict[str, Any] = {}
    for name, prop_schema in schema.get("properties", {}).items():
        if not isinstance(prop_schema, dict):
            continue
        if "default" in prop_schema:
            arguments[name] = prop_schema["default"]
        elif prop_schema.get("examples"):
            arguments[name] = prop_schema["examples"][0]
    return arguments


def _submit_call(context: dict[str, Any]) -> LLMToolCall:
    candidate_actions = context.get("candidate_actions") or []
    available_evidence = context.get("available_evidence") or []

    selected_action_id: str | None = None
    if isinstance(candidate_actions, list) and candidate_actions:
        ranked = [action for action in candidate_actions if isinstance(action, dict)]
        if ranked:
            best = min(ranked, key=lambda action: action.get("rank", 0))
            selected_action_id = best.get("action_id")

    cited_evidence_ids: list[str] = []
    if selected_action_id is not None and isinstance(available_evidence, list):
        cited_evidence_ids = [
            evidence["evidence_id"]
            for evidence in available_evidence
            if isinstance(evidence, dict) and evidence.get("evidence_id")
        ]

    if selected_action_id is not None:
        summary = f"[Fixture] Selected action {selected_action_id} (lowest rank)."
        rationale = "Deterministic fixture selection: lowest-rank candidate action."
    else:
        summary = "[Fixture] No candidate action was available in the provided context."
        rationale = "No candidate_actions were present to select from."

    arguments = {
        "summary": summary,
        "selected_action_id": selected_action_id,
        "action_rationale": rationale,
        "finding_notes": [],
        "revision_note": None,
        "cited_evidence_ids": cited_evidence_ids,
    }
    return LLMToolCall(id=f"fixture-{SUBMIT_TOOL_NAME}", name=SUBMIT_TOOL_NAME, arguments=arguments)


def default_fixture_script(request: FixtureRequest) -> LLMResponse:
    investigative_tools = [tool for tool in request.tools if tool.name != SUBMIT_TOOL_NAME]
    needed = min(2, len(investigative_tools))
    tool_results_seen = _tool_result_count(request.messages)

    tool_call: LLMToolCall | None = None
    if tool_results_seen < needed:
        called = _called_tool_names(request.messages)
        next_tool = next((tool for tool in investigative_tools if tool.name not in called), None)
        if next_tool is not None:
            arguments = _arguments_from_schema(next_tool.input_schema)
            tool_call = LLMToolCall(
                id=f"fixture-{next_tool.name}", name=next_tool.name, arguments=arguments
            )

    if tool_call is None:
        tool_call = _submit_call(_extract_context(request.messages))

    input_tokens = len(json.dumps(request.messages)) // 4
    output_tokens = len(json.dumps(tool_call.arguments)) // 4

    return LLMResponse(
        text=None,
        tool_calls=[tool_call],
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider="fixture",
        model=FixtureLLMClient.model,
        request_id=None,
        raw_content=None,
    )


class FixtureLLMClient:
    provider = "fixture"
    model = "fixture-scripted-v1"

    def __init__(self, script: FixtureScript | None = None) -> None:
        self._script = script or default_fixture_script

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[LLMToolSpec],
        max_tokens: int,
        timeout_seconds: float,
    ) -> LLMResponse:
        request = FixtureRequest(system=system, messages=list(messages), tools=list(tools))
        return self._script(request)
