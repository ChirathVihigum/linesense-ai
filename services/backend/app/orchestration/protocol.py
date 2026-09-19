"""The agent task protocol (backend-contracts.md section 6, ``schema_version "1.0"``).

A custom, versioned HTTP/JSON protocol between the orchestrator and the agent
executor. It is **not** A2A or MCP and must never be labelled so.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"

Recipient = Literal["planning", "rm", "ie", "quality"]
RECIPIENTS: tuple[str, ...] = ("planning", "rm", "ie", "quality")

ALLOWED_TASK_TYPES: dict[str, frozenset[str]] = {
    "rm": frozenset({"assess_material_readiness", "validate_plan_materials"}),
    "ie": frozenset({"assess_line_capability"}),
    "planning": frozenset({"propose_allocation", "revise_allocation"}),
    "quality": frozenset({"assess_quality_status"}),
}


class AgentErrorCode(StrEnum):
    MISSING_DATA = "MISSING_DATA"
    STALE_INPUT = "STALE_INPUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_AGENT_OUTPUT = "INVALID_AGENT_OUTPUT"
    POLICY_DENIED = "POLICY_DENIED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"


RETRYABLE: dict[AgentErrorCode, bool] = {code: False for code in AgentErrorCode} | {
    AgentErrorCode.PROVIDER_UNAVAILABLE: True
}


def idempotency_key_for(
    run_id: uuid.UUID, recipient: str, snapshot_id: uuid.UUID, round_: int
) -> str:
    """``f"{run_id}:{recipient}:{snapshot_id}:round-{round}"``."""
    return f"{run_id}:{recipient}:{snapshot_id}:round-{round_}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


InputRefType = Literal[
    "order", "snapshot", "agent_result", "material_balance", "capacity_slot", "policy"
]


class InputRef(_Strict):
    type: InputRefType
    id: uuid.UUID
    version: int | None = None


class TaskConstraints(_Strict):
    max_tool_calls: int = Field(4, ge=0, le=4)
    read_only: Literal[True] = True


class TaskEnvelope(_Strict):
    schema_version: Literal["1.0"]
    message_id: uuid.UUID
    run_id: uuid.UUID
    parent_task_id: uuid.UUID | None
    organization_id: uuid.UUID
    factory_id: uuid.UUID
    order_id: uuid.UUID
    snapshot_id: uuid.UUID
    sender: Literal["orchestrator"]
    recipient: Recipient
    task_type: str
    idempotency_key: str = Field(min_length=1, max_length=300)
    round: int = Field(ge=0, le=1)
    # Timezone-aware only: a naive value would raise TypeError the moment it is
    # compared with the run's timestamptz deadline.
    deadline_at: AwareDatetime
    input_refs: list[InputRef]
    constraints: TaskConstraints
    trace_id: str = Field(max_length=200)

    @model_validator(mode="after")
    def _task_type_allowed(self) -> TaskEnvelope:
        if self.task_type not in ALLOWED_TASK_TYPES[self.recipient]:
            raise ValueError(
                f"task_type {self.task_type!r} is not allowed for recipient {self.recipient!r}"
            )
        return self


class DispatchReceipt(BaseModel):
    task_id: uuid.UUID
    run_id: uuid.UUID
    status: str


class EvidenceRef(_Strict):
    evidence_id: str = Field(min_length=1, max_length=100)
    kind: Literal["record", "document", "calculation"]
    record_type: str | None = None
    record_id: uuid.UUID | None = None
    record_version: int | None = None
    document_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    page_number: int | None = None
    section: str | None = None
    description: str


class Finding(_Strict):
    finding_id: str
    severity: Literal["info", "warning", "critical"]
    code: str
    message: str
    evidence_ids: list[str]
    source: Literal["deterministic", "model"]


class Metric(_Strict):
    name: str
    value: Decimal | None
    unit: str
    note: str | None = None


ActionKind = Literal[
    "ALLOCATION",
    "RESERVATION",
    "ALLOCATION_AND_RESERVATION",
    "REPLENISHMENT_SUGGESTION",
    "QUALITY_HOLD_REVIEW",
    "IE_REVIEW",
]


class RecommendedAction(_Strict):
    action_id: str
    kind: ActionKind
    summary: str
    payload: dict[str, Any]
    evidence_ids: list[str]
    rank: int
    source: Literal["deterministic", "model"]


class DataQuality(_Strict):
    complete: bool
    missing: list[str]
    notes: list[str]


class ExecutionMetadata(_Strict):
    provider: str
    model: str
    model_calls: int
    tool_calls: list[str]
    input_tokens: int
    output_tokens: int
    prompt_version: str
    degraded: bool
    degraded_reason: str | None
    started_at: datetime
    completed_at: datetime


class AgentResult(_Strict):
    schema_version: Literal["1.0"]
    task_id: uuid.UUID
    agent: Recipient
    status: Literal["SUCCEEDED", "DEGRADED", "FAILED"]
    summary: str
    summary_source: Literal["deterministic", "model"]
    findings: list[Finding]
    metrics: list[Metric]
    recommended_actions: list[RecommendedAction]
    evidence_refs: list[EvidenceRef]
    warnings: list[str]
    input_versions: dict[str, Any]
    data_quality: DataQuality
    execution_metadata: ExecutionMetadata
    error_code: AgentErrorCode | None = None
