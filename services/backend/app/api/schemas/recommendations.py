"""Request/response schemas for recommendations (task-14-brief.md requirement 4)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.runs import LlmLabel, OrderRef, UserRef

REASON_MAX_LENGTH = 500


class DecisionRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    reason: str | None = Field(default=None, max_length=REASON_MAX_LENGTH)
    proposal_hash: str = Field(min_length=1, max_length=128)


class ApplyRequest(BaseModel):
    proposal_hash: str = Field(min_length=1, max_length=128)


class RecommendationOrderRef(OrderRef):
    production_state: str
    material_state: str
    quantity: int
    due_date: date
    version: int


class RunRefOut(BaseModel):
    id: uuid.UUID
    status: str
    llm: LlmLabel


class DecisionOut(BaseModel):
    decision: str
    decided_by: UserRef
    decided_at: datetime
    reason: str


class StaleInputOut(BaseModel):
    kind: str
    id: uuid.UUID
    expected_version: int
    current_version: int | None


class EvidenceOut(BaseModel):
    agent: str
    evidence_id: str
    kind: str
    description: str
    record_type: str | None
    record_id: uuid.UUID | None
    record_version: int | None
    document_id: uuid.UUID | None
    document_title: str | None
    document_version_no: int | None
    page_number: int | None
    section: str | None
    chunk_id: uuid.UUID | None
    citation_url: str | None


class SlotDiffOut(BaseModel):
    slot_id: uuid.UUID
    line_code: str
    slot_date: date
    shift_code: str
    capacity: Decimal
    allocated_before: Decimal
    allocated_after: Decimal
    remaining_after: Decimal
    utilization_after: Decimal | None
    standard_minutes: Decimal
    units: Decimal


class ReservationDiffOut(BaseModel):
    material_id: uuid.UUID
    material_code: str
    quantity: Decimal
    unit: str
    on_hand: Decimal
    reserved_before: Decimal
    reserved_after: Decimal
    available_after: Decimal


class ProposalDiff(BaseModel):
    slots: list[SlotDiffOut]
    reservations: list[ReservationDiffOut]


class RecommendationSummary(BaseModel):
    id: uuid.UUID
    order: OrderRef
    run_id: uuid.UUID
    kind: str
    status: str
    generated_by: str
    proposed_by_agent: str
    proposer: UserRef
    rationale: str
    proposal_hash: str
    status_source: str
    expires_at: datetime
    expired: bool
    superseded_reason: str | None
    applied_at: datetime | None
    created_at: datetime


class RecommendationDetailOut(BaseModel):
    id: uuid.UUID
    order: RecommendationOrderRef
    run: RunRefOut
    kind: str
    status: str
    generated_by: str
    proposed_by_agent: str
    proposer: UserRef
    rationale: str
    proposal: dict[str, object]
    proposal_hash: str
    input_versions: dict[str, object]
    status_source: str
    decision: DecisionOut | None
    evidence: list[EvidenceOut]
    diff: ProposalDiff
    stale: bool
    stale_inputs: list[StaleInputOut]
    expired: bool
    can_decide: bool
    decide_blocked_reason: str | None
    can_apply: bool
    apply_blocked_reason: str | None
    expires_at: datetime
    superseded_reason: str | None
    applied_at: datetime | None
    created_at: datetime
    version: int


class DecisionResult(BaseModel):
    id: uuid.UUID
    status: str
    decision: str
    decided_by: UserRef
    decided_at: datetime
    reason: str
    version: int


class AppliedAllocationOut(BaseModel):
    id: uuid.UUID
    slot_id: uuid.UUID
    standard_minutes: Decimal
    units: Decimal


class AppliedReservationOut(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    quantity: Decimal


class AppliedOrderOut(BaseModel):
    id: uuid.UUID
    external_ref: str
    production_state: str
    material_state: str
    version: int


class ApplyResultOut(BaseModel):
    id: uuid.UUID
    status: str
    applied_at: datetime
    order: AppliedOrderOut
    allocations: list[AppliedAllocationOut]
    reservations: list[AppliedReservationOut]
    released_allocations: int
    released_reservations: int
