"""Request/response schemas for analysis runs (task-12-brief.md requirement 2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.orchestration.protocol import AgentResult

FIXTURE_LABEL = "Test fixture — not a live AI model"
DISABLED_LABEL = "AI disabled — deterministic results only"


class AnalysisRequest(BaseModel):
    expected_order_version: int = Field(ge=1)


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: str


class OrderRef(BaseModel):
    id: uuid.UUID
    external_ref: str


class UserRef(BaseModel):
    id: uuid.UUID
    display_name: str


class LlmLabel(BaseModel):
    provider: str
    model: str
    is_fixture: bool
    label: str


class TaskOut(BaseModel):
    id: uuid.UUID
    recipient: str
    task_type: str
    round: int
    status: str
    attempt: int
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    parent_task_id: uuid.UUID | None


class RecommendationOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    generated_by: str
    proposed_by_agent: str
    rationale: str
    proposal_hash: str
    expires_at: datetime
    superseded_reason: str | None
    created_at: datetime


class RunSummary(BaseModel):
    id: uuid.UUID
    order: OrderRef
    status: str
    requested_by: UserRef
    llm: LlmLabel
    degraded_reason: str | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class RunDetail(RunSummary):
    model_calls_used: int
    model_calls_limit: int
    tokens_used: int
    replan_count: int
    started_at: datetime | None
    deadline_at: datetime
    tasks: list[TaskOut]
    results: list[AgentResult]
    recommendations: list[RecommendationOut]
    report: dict[str, Any] | None


class RunEventOut(BaseModel):
    id: int
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime


def llm_label(provider: str, model: str) -> LlmLabel:
    """How a run's provider is shown, per backend-contracts.md section 8."""
    if provider == "fixture":
        label = FIXTURE_LABEL
    elif provider == "disabled":
        label = DISABLED_LABEL
    else:
        label = f"{provider} · {model}"
    return LlmLabel(provider=provider, model=model, is_fixture=provider == "fixture", label=label)
