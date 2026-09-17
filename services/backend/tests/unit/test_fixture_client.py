"""Deterministic behaviour of the fixture LLM client (no network access)."""

from __future__ import annotations

import asyncio
import json

from app.llm.client import LLMToolSpec
from app.llm.fixture_client import FixtureLLMClient, default_fixture_script

LOOKUP_TOOL = LLMToolSpec(
    name="lookup_material_balance",
    description="Look up a material balance",
    input_schema={
        "type": "object",
        "properties": {
            "material_id": {"type": "string", "default": "mat-1"},
        },
    },
)
LOOKUP_TOOL_2 = LLMToolSpec(
    name="lookup_capacity_slot",
    description="Look up a capacity slot",
    input_schema={
        "type": "object",
        "properties": {
            "slot_id": {"type": "string", "examples": ["slot-1"]},
        },
    },
)
SUBMIT_TOOL = LLMToolSpec(
    name="submit_assessment",
    description="Submit the assessment",
    input_schema={"type": "object", "properties": {}},
)

CONTEXT = {
    "candidate_actions": [
        {"action_id": "act-2", "rank": 2},
        {"action_id": "act-1", "rank": 1},
        {"action_id": "act-3", "rank": 3},
    ],
    "available_evidence": [
        {"evidence_id": "ev-1"},
        {"evidence_id": "ev-2"},
    ],
}


def _system_message() -> dict[str, object]:
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": f"Assess this order.\n<context>{json.dumps(CONTEXT)}</context>",
            }
        ],
    }


def _tool_result(tool_use_id: str) -> dict[str, object]:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}],
    }


def _tool_use(name: str) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": f"call-{name}", "name": name, "input": {}}],
    }


def _run(client: FixtureLLMClient, messages: list[dict[str, object]]) -> object:
    return asyncio.run(
        client.complete(
            system="system prompt",
            messages=messages,
            tools=[LOOKUP_TOOL, LOOKUP_TOOL_2, SUBMIT_TOOL],
            max_tokens=1024,
            timeout_seconds=30,
        )
    )


def test_calls_investigative_tools_before_submitting() -> None:
    client = FixtureLLMClient()
    messages = [_system_message()]

    first = _run(client, messages)
    assert first.provider == "fixture"
    assert len(first.tool_calls) == 1
    assert first.tool_calls[0].name == LOOKUP_TOOL.name
    assert first.tool_calls[0].arguments == {"material_id": "mat-1"}
    assert first.stop_reason == "tool_use"

    messages = [*messages, _tool_use(LOOKUP_TOOL.name), _tool_result("call-1")]
    second = _run(client, messages)
    assert second.tool_calls[0].name == LOOKUP_TOOL_2.name
    assert second.tool_calls[0].arguments == {"slot_id": "slot-1"}

    messages = [*messages, _tool_use(LOOKUP_TOOL_2.name), _tool_result("call-2")]
    third = _run(client, messages)
    assert third.tool_calls[0].name == "submit_assessment"


def test_submits_lowest_rank_action_citing_all_evidence() -> None:
    client = FixtureLLMClient()
    messages = [
        _system_message(),
        _tool_use(LOOKUP_TOOL.name),
        _tool_result("call-1"),
        _tool_use(LOOKUP_TOOL_2.name),
        _tool_result("call-2"),
    ]

    response = _run(client, messages)
    call = response.tool_calls[0]
    assert call.name == "submit_assessment"
    assert call.arguments["selected_action_id"] == "act-1"
    assert call.arguments["cited_evidence_ids"] == ["ev-1", "ev-2"]
    assert call.arguments["summary"].startswith("[Fixture] ")
    assert call.arguments["revision_note"] is None
    assert response.stop_reason == "tool_use"


def test_missing_context_submits_with_no_selection() -> None:
    client = FixtureLLMClient()
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "no context tags here"}]},
        _tool_use(LOOKUP_TOOL.name),
        _tool_result("call-1"),
        _tool_use(LOOKUP_TOOL_2.name),
        _tool_result("call-2"),
    ]

    response = _run(client, messages)
    call = response.tool_calls[0]
    assert call.arguments["selected_action_id"] is None
    assert call.arguments["cited_evidence_ids"] == []


def test_deterministic_same_input_same_output() -> None:
    client = FixtureLLMClient()
    messages = [_system_message()]

    first = _run(client, messages)
    second = _run(client, messages)

    assert first.model_dump() == second.model_dump()


def test_default_fixture_script_is_the_default() -> None:
    client = FixtureLLMClient()
    assert client._script is default_fixture_script  # noqa: SLF001
    assert client.provider == "fixture"
    assert client.model == "fixture-scripted-v1"


def test_custom_script_is_used() -> None:
    from app.llm.client import LLMResponse
    from app.llm.fixture_client import FixtureRequest

    def _custom(request: FixtureRequest) -> LLMResponse:
        return LLMResponse(
            text="custom",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
            provider="fixture",
            model="fixture-scripted-v1",
            request_id=None,
        )

    client = FixtureLLMClient(script=_custom)
    response = _run(client, [_system_message()])
    assert response.text == "custom"
