"""Unit tests for `app.nlp.summarize` (task-18-brief.md requirement 4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.llm.client import (
    LLMDisabledError,
    LLMResponse,
    LLMToolSpec,
    LLMUnavailableError,
)
from app.nlp.summarize import (
    DISABLED_LABEL,
    FIXTURE_LABEL,
    REJECTED_LABEL,
    UNAVAILABLE_LABEL,
    grounded_summary,
    model_summary,
)


def _report(*, eligible: bool = True) -> dict[str, Any]:
    return {
        "order": {"id": "order-1", "external_ref": "PO-KTN-0001", "due_date": "2026-10-01"},
        "states": {
            "production": "IN_PRODUCTION",
            "material": "AT_RISK",
            "quality": "NOT_INSPECTED",
            "analysis": "COMPLETED",
        },
        "shipment": {
            "eligible": eligible,
            "reasons": [] if eligible else ["Quality pending — no inspection recorded"],
            "source": "Calculated from records",
        },
        "blockers": [
            {
                "code": "MATERIAL_SHORTAGE",
                "message": "M03 is short by 40 m.",
                "agent": "rm",
                "severity": "critical",
                "evidence_ids": ["ev-1"],
                "source": "deterministic",
            }
        ],
        "agent_summaries": [],
        "recommendation": {
            "id": "rec-1",
            "status": "PROPOSED",
            "kind": "ALLOCATION",
            "source_label": "Calculated from records",
        },
        "evidence": [
            {"evidence_id": "ev-1", "kind": "record", "description": "material balance M03"},
        ],
        "degraded": False,
        "degraded_reasons": [],
        "generated_at": "2026-09-20T00:00:00Z",
    }


def _all_evidence_ids(report: dict[str, Any]) -> set[str]:
    return {entry["evidence_id"] for entry in report["evidence"]}


def test_every_deterministic_sentence_has_evidence_ids_that_exist() -> None:
    report = _report(eligible=False)
    summary = grounded_summary(report)
    valid = _all_evidence_ids(report)
    assert summary.summary_source == "deterministic"
    assert summary.label is None
    assert len(summary.sentences) >= 1
    for sentence in summary.sentences:
        assert all(evidence_id in valid for evidence_id in sentence.evidence_ids)


def test_deterministic_summary_covers_states_shipment_blockers_and_recommendation() -> None:
    report = _report(eligible=False)
    summary = grounded_summary(report)
    joined = " ".join(sentence.text for sentence in summary.sentences)
    assert "IN_PRODUCTION" in joined
    assert "not eligible" in joined
    assert "M03 is short by 40 m." in joined
    assert "PROPOSED" in joined


@dataclass
class FakeLLMClient:
    provider: str
    model: str = "fake-model"
    response_text: str | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[LLMToolSpec],
        max_tokens: int,
        timeout_seconds: float,
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages})
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text=self.response_text,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=10,
            provider=self.provider,
            model=self.model,
            request_id="req-1",
        )


async def _always_allow() -> bool:
    return True


async def _always_deny() -> bool:
    return False


async def test_model_summary_returns_none_when_budget_is_exhausted() -> None:
    report = _report()
    llm = FakeLLMClient(provider="anthropic", response_text="{}")
    result = await model_summary(report, llm, _always_deny)
    assert result is None
    assert llm.calls == []


async def test_model_summary_accepts_a_valid_grounded_response() -> None:
    report = _report()
    payload = json.dumps(
        {"sentences": [{"text": "Material M03 is short.", "evidence_ids": ["ev-1"]}]}
    )
    llm = FakeLLMClient(provider="anthropic", response_text=payload)
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "model"
    assert result.label is None
    assert result.sentences[0].text == "Material M03 is short."
    assert result.sentences[0].evidence_ids == ("ev-1",)


async def test_model_summary_labels_the_fixture_provider() -> None:
    report = _report()
    payload = json.dumps(
        {"sentences": [{"text": "Material M03 is short.", "evidence_ids": ["ev-1"]}]}
    )
    llm = FakeLLMClient(provider="fixture", response_text=payload)
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "model"
    assert result.label == FIXTURE_LABEL


async def test_model_summary_rejects_a_shipment_claim_that_contradicts_eligibility() -> None:
    report = _report(eligible=False)
    payload = json.dumps(
        {"sentences": [{"text": "All clear, ready to ship.", "evidence_ids": ["ev-1"]}]}
    )
    llm = FakeLLMClient(provider="anthropic", response_text=payload)
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "deterministic"
    assert result.label == REJECTED_LABEL


async def test_model_summary_rejects_an_unknown_evidence_id() -> None:
    report = _report()
    payload = json.dumps(
        {"sentences": [{"text": "Material M03 is short.", "evidence_ids": ["ev-does-not-exist"]}]}
    )
    llm = FakeLLMClient(provider="anthropic", response_text=payload)
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "deterministic"
    assert result.label == REJECTED_LABEL


async def test_model_summary_rejects_a_sentence_without_evidence_ids() -> None:
    report = _report()
    payload = json.dumps({"sentences": [{"text": "Material M03 is short.", "evidence_ids": []}]})
    llm = FakeLLMClient(provider="anthropic", response_text=payload)
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "deterministic"
    assert result.label == REJECTED_LABEL


async def test_model_summary_rejects_unparsable_json() -> None:
    report = _report()
    llm = FakeLLMClient(provider="anthropic", response_text="not json at all")
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "deterministic"
    assert result.label == REJECTED_LABEL


async def test_model_summary_falls_back_when_provider_disabled() -> None:
    report = _report()
    llm = FakeLLMClient(provider="disabled", error=LLMDisabledError("disabled"))
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "deterministic"
    assert result.label == DISABLED_LABEL


async def test_model_summary_falls_back_when_provider_unavailable() -> None:
    report = _report()
    llm = FakeLLMClient(provider="anthropic", error=LLMUnavailableError("boom"))
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    assert result.summary_source == "deterministic"
    assert result.label == UNAVAILABLE_LABEL


@pytest.mark.parametrize("eligible", [True, False])
async def test_fallback_summaries_still_only_cite_evidence_that_exists(eligible: bool) -> None:
    report = _report(eligible=eligible)
    llm = FakeLLMClient(provider="anthropic", error=LLMUnavailableError("boom"))
    result = await model_summary(report, llm, _always_allow)
    assert result is not None
    valid = _all_evidence_ids(report)
    for sentence in result.sentences:
        assert all(evidence_id in valid for evidence_id in sentence.evidence_ids)
