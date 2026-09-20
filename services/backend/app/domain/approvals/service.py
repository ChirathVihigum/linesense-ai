"""Approvals: deciding a proposal and applying it in one fenced transaction
(task-14-brief.md).

Integrity rules, all enforced here rather than in the routes:

* **Separation of duties** — the person who requested the analysis that
  produced a proposal may neither decide nor apply it
  (403 ``SELF_APPROVAL_DENIED``, audited ``DENIED``).
* **Deciding never applies** — an approval only records intent; every
  balance and slot change happens later, in `apply`.
* **Freshness** — a proposal carries the versions of every input it was
  derived from. `apply` re-reads those versions *inside the transaction
  that holds the row locks*; any drift supersedes the recommendation
  (committed) and rejects the apply with 409 ``STALE_INPUT``. An expired
  proposal is marked ``EXPIRED`` (also committed) and rejected with 409
  ``EXPIRED``.
* **Lock order** — order row -> `line_capacity_slots` -> `material_balances`,
  each group ``SELECT ... FOR UPDATE`` by ascending id, matching
  `app.domain.capacity.service` and `app.domain.inventory.service`. The
  recommendation row itself is locked immediately after its order row. The
  union of *every* slot/balance the transaction will touch (the proposal's
  own rows plus those of the order's existing ACTIVE allocations and
  reservations) is locked up front, so the release/allocate/reserve helpers
  never need a lock the transaction does not already hold.

`PersistedRejection` is the one subtlety: two rejection paths (expiry and
staleness) must *commit* the status change they record and only then fail
the request. The caller (`app.api.recommendations`) commits the session
when it sees this exception and re-raises it as the error response.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    Allocation,
    AnalysisRun,
    Approval,
    BomLine,
    Chunk,
    Document,
    DocumentVersion,
    Factory,
    Line,
    LineCapacitySlot,
    Material,
    MaterialBalance,
    Notification,
    Order,
    Recommendation,
    Reservation,
    User,
)
from app.domain.capacity.service import allocate, lock_slots, release_order_allocations
from app.domain.clock import utcnow
from app.domain.inventory.calc import available_now
from app.domain.inventory.service import (
    ensure_balance,
    lock_balances,
    recompute_material_states,
    release_order_reservations,
    reserve_material,
)
from app.domain.planning.calc import utilization
from app.domain.vocab import (
    ActorType,
    ApprovalDecision,
    AuditOutcome,
    GeneratedBy,
    ProductionState,
    RecommendationStatus,
    Role,
)
from app.jobs.queue import enqueue

DECIDE_PERMISSION = "recommendation:decide"
APPLY_PERMISSION = "recommendation:apply"
READ_PERMISSION = "analysis:read"

DECIDE_ACTION = "recommendation.decided"
APPLY_ACTION = "recommendation.applied"

REFRESH_MATERIAL_STATES_JOB = "maintenance.refresh_material_states"
"""Kept in sync with `app.jobs.handlers.MAINTENANCE_REFRESH_MATERIAL_STATES`."""

APPLICABLE_ORDER_STATES = (ProductionState.VALIDATED.value, ProductionState.PLANNED.value)
REASON_MIN_LENGTH = 3
REASON_MAX_LENGTH = 500
STALE_REASON_PREFIX = "STALE_INPUT: "
STALE_NOTIFICATION_BODY = "Inputs changed — run a new analysis."
CITATION_URL_TEMPLATE = "/api/v1/citations/{chunk_id}"

# `blocked_reason` values (task-14-brief.md requirement 1).
BLOCKED_SELF_APPROVAL = "SELF_APPROVAL"
BLOCKED_MISSING_PERMISSION = "MISSING_PERMISSION"
BLOCKED_STALE = "STALE"
BLOCKED_EXPIRED = "EXPIRED"
BLOCKED_WRONG_STATUS = "WRONG_STATUS"

STATUS_SOURCE_MODEL = "AI recommendation"
STATUS_SOURCE_DETERMINISTIC = "Calculated from records"

ORDER_KIND = "order"
SLOT_KIND = "capacity_slot"
BALANCE_KIND = "material_balance"


class PersistedRejection(AppError):
    """An error whose transaction must be **committed** before it is raised.

    Used for the two rejections that are themselves state changes: an
    expired proposal becomes ``EXPIRED`` and a stale one ``SUPERSEDED``.
    """


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StaleInput:
    kind: str
    id: uuid.UUID
    expected_version: int | None
    """``None`` when the proposal recorded no usable version for this row."""
    current_version: int | None


@dataclass(frozen=True)
class EvidenceItem:
    agent: str
    evidence_id: str
    kind: str
    description: str
    record_type: str | None = None
    record_id: uuid.UUID | None = None
    record_version: int | None = None
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    document_version_no: int | None = None
    page_number: int | None = None
    section: str | None = None
    chunk_id: uuid.UUID | None = None
    citation_url: str | None = None


@dataclass(frozen=True)
class SlotDiff:
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


@dataclass(frozen=True)
class ReservationDiff:
    material_id: uuid.UUID
    material_code: str
    quantity: Decimal
    unit: str
    on_hand: Decimal
    reserved_before: Decimal
    reserved_after: Decimal
    available_after: Decimal


@dataclass(frozen=True)
class UserRef:
    id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class RunRef:
    id: uuid.UUID
    status: str
    llm_provider: str
    llm_model: str


@dataclass(frozen=True)
class DecisionRef:
    decision: str
    decided_by: UserRef
    decided_at: datetime
    reason: str


@dataclass(frozen=True)
class RecommendationRow:
    """List row: the recommendation plus the labels the board needs."""

    recommendation: Recommendation
    order: Order
    proposer: UserRef
    expired: bool
    status_source: str


@dataclass(frozen=True)
class RecommendationDetail:
    recommendation: Recommendation
    order: Order
    run: RunRef
    proposer: UserRef
    decision: DecisionRef | None
    evidence: list[EvidenceItem]
    slots: list[SlotDiff]
    reservations: list[ReservationDiff]
    stale: bool
    stale_inputs: list[StaleInput]
    expired: bool
    can_decide: bool
    decide_blocked_reason: str | None
    can_apply: bool
    apply_blocked_reason: str | None
    status_source: str


@dataclass(frozen=True)
class AppliedAllocation:
    id: uuid.UUID
    slot_id: uuid.UUID
    standard_minutes: Decimal
    units: Decimal


@dataclass(frozen=True)
class AppliedReservation:
    id: uuid.UUID
    material_id: uuid.UUID
    quantity: Decimal


@dataclass(frozen=True)
class ApplyResult:
    recommendation_id: uuid.UUID
    status: str
    applied_at: datetime
    order: Order
    allocations: list[AppliedAllocation]
    reservations: list[AppliedReservation]
    released_allocations: int
    released_reservations: int


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _rows(proposal: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(proposal, dict):
        return []
    rows = proposal.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal(0)


def _expected_versions(section: Any) -> dict[uuid.UUID, int]:
    """``{id: version}`` from one ``input_versions`` section, skipping junk."""
    if not isinstance(section, dict):
        return {}
    expected: dict[uuid.UUID, int] = {}
    for key, value in section.items():
        row_id = _uuid(key)
        if row_id is None or not isinstance(value, int) or isinstance(value, bool):
            continue
        expected[row_id] = value
    return expected


def _status_source(recommendation: Recommendation, decision: DecisionRef | None) -> str:
    if decision is not None:
        verb = "Approved" if decision.decision == ApprovalDecision.APPROVED.value else "Rejected"
        return (
            f"{verb} by {decision.decided_by.display_name} at "
            f"{decision.decided_at.isoformat(timespec='seconds')}"
        )
    if recommendation.generated_by == GeneratedBy.MODEL.value:
        return STATUS_SOURCE_MODEL
    return STATUS_SOURCE_DETERMINISTIC


def _is_expired(recommendation: Recommendation, *, now: datetime | None = None) -> bool:
    return recommendation.expires_at <= (now or utcnow())


async def _lock_recommendation(session: AsyncSession, rec_id: uuid.UUID) -> Recommendation:
    """Re-select the recommendation ``FOR UPDATE`` (after its order row)."""
    return (
        await session.scalars(
            select(Recommendation)
            .where(Recommendation.id == rec_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()


async def _lock_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    return (
        await session.scalars(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()


async def _user_ref(session: AsyncSession, user_id: uuid.UUID) -> UserRef:
    user = await session.get(User, user_id)
    if user is None:
        raise RuntimeError(f"recommendation references a missing user {user_id}")
    return UserRef(id=user.id, display_name=user.display_name)


async def _notify(
    session: AsyncSession,
    recommendation: Recommendation,
    *,
    user_id: uuid.UUID | None,
    role: str | None,
    kind: str,
    title: str,
    body: str,
) -> None:
    session.add(
        Notification(
            organization_id=recommendation.organization_id,
            factory_id=recommendation.factory_id,
            user_id=user_id,
            role=role,
            kind=kind,
            title=title,
            body=body,
            link=f"/recommendations/{recommendation.id}",
        )
    )
    await session.flush()


async def _audit(
    session: AsyncSession,
    recommendation: Recommendation,
    principal: Principal,
    *,
    action: str,
    outcome: str,
    reason: str | None = None,
    trace_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    await record_audit(
        session,
        organization_id=recommendation.organization_id,
        factory_id=recommendation.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action=action,
        target_type="recommendation",
        target_id=str(recommendation.id),
        outcome=outcome,
        reason=reason,
        trace_id=trace_id,
        run_id=recommendation.run_id,
        before=before,
        after=after,
    )


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


async def _current_versions(
    session: AsyncSession, id_column: Any, version_column: Any, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = (
        await session.execute(select(id_column, version_column).where(id_column.in_(sorted(ids))))
    ).all()
    return {row[0]: row[1] for row in rows}


def _required_ids(rec: Recommendation, key: str, field: str) -> set[uuid.UUID]:
    """The ids of one kind of row the proposal would actually write to."""
    found: set[uuid.UUID] = set()
    for row in _rows(rec.proposal, key):
        row_id = _uuid(row.get(field))
        if row_id is not None:
            found.add(row_id)
    return found


async def check_staleness(session: AsyncSession, rec: Recommendation) -> list[StaleInput]:
    """Every proposal input whose current version differs from the one the
    proposal was computed against.

    **Fails closed.** A row the proposal would write to but that
    ``input_versions`` does not pin — missing from the section, keyed by
    something that is not a UUID, or carrying a version that is not an
    integer — is reported as stale with ``expected_version=None``, because
    nothing proves it has not moved since. A row that no longer exists is
    stale too (``current_version=None``). Versions recorded for rows the
    proposal does not touch are still checked.

    Call *after* taking the row locks when the result must be authoritative;
    the detail view calls it on plain reads, where it is advisory.
    """
    versions = rec.input_versions if isinstance(rec.input_versions, dict) else {}
    sections: tuple[tuple[str, str, Any, Any, set[uuid.UUID]], ...] = (
        (ORDER_KIND, "order", Order.id, Order.version, {rec.order_id}),
        (
            SLOT_KIND,
            "capacity_slots",
            LineCapacitySlot.id,
            LineCapacitySlot.version,
            _required_ids(rec, "allocations", "slot_id"),
        ),
        (
            BALANCE_KIND,
            "material_balances",
            MaterialBalance.id,
            MaterialBalance.version,
            _required_ids(rec, "reservations", "balance_id"),
        ),
    )
    stale: list[StaleInput] = []
    for kind, section, id_column, version_column, required in sections:
        expected = _expected_versions(versions.get(section))
        checked = set(expected) | required
        current = await _current_versions(session, id_column, version_column, checked)
        for row_id in sorted(checked, key=str):
            want = expected.get(row_id)
            have = current.get(row_id)
            if want is None or have != want:
                stale.append(
                    StaleInput(kind=kind, id=row_id, expected_version=want, current_version=have)
                )
    return stale


def _stale_field_errors(stale_inputs: Sequence[StaleInput]) -> list[dict[str, str]]:
    return [
        {
            "field": item.kind,
            "message": (
                f"{item.id} is at version "
                f"{'(deleted)' if item.current_version is None else item.current_version}"
                + (
                    "; the proposal recorded no version for it."
                    if item.expected_version is None
                    else f"; the proposal was computed against version {item.expected_version}."
                )
            ),
        }
        for item in stale_inputs
    ]


def _stale_reason(stale_inputs: Sequence[StaleInput]) -> str:
    kinds = sorted({item.kind for item in stale_inputs})
    return STALE_REASON_PREFIX + ", ".join(kinds)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


async def list_recommendations(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    status: str | None = None,
    *,
    limit: int,
    offset: int,
) -> tuple[list[RecommendationRow], int]:
    """The factory's recommendations, newest first, optionally by ``status``."""
    await load_scoped(session, Factory, factory_id, principal, READ_PERMISSION)
    base = select(Recommendation).where(Recommendation.factory_id == factory_id)
    if status is not None:
        base = base.where(Recommendation.status == status)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    recommendations = list(
        (
            await session.scalars(
                base.order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    now = utcnow()
    rows: list[RecommendationRow] = []
    for recommendation in recommendations:
        order = await session.get(Order, recommendation.order_id)
        if order is None:
            raise RuntimeError(f"recommendation {recommendation.id} references a missing order")
        decision = await decision_ref(session, recommendation)
        rows.append(
            RecommendationRow(
                recommendation=recommendation,
                order=order,
                proposer=await _user_ref(session, recommendation.proposer_user_id),
                expired=_is_expired(recommendation, now=now),
                status_source=_status_source(recommendation, decision),
            )
        )
    return rows, int(total or 0)


async def decision_ref(session: AsyncSession, rec: Recommendation) -> DecisionRef | None:
    approval = await session.scalar(select(Approval).where(Approval.recommendation_id == rec.id))
    if approval is None:
        return None
    return DecisionRef(
        decision=approval.decision,
        decided_by=await _user_ref(session, approval.decided_by),
        decided_at=approval.decided_at,
        reason=approval.reason,
    )


async def _evidence(session: AsyncSession, rec: Recommendation) -> list[EvidenceItem]:
    """Resolve the stored evidence refs into something displayable."""
    stored = rec.evidence_refs if isinstance(rec.evidence_refs, dict) else {}
    items = stored.get("items")
    if not isinstance(items, list):
        return []
    resolved: list[EvidenceItem] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        chunk_id = _uuid(raw.get("chunk_id"))
        document_id = _uuid(raw.get("document_id"))
        version_id = _uuid(raw.get("document_version_id"))
        page_number = raw.get("page_number")
        section = raw.get("section")
        if chunk_id is not None:
            chunk = await session.get(Chunk, chunk_id)
            if chunk is not None:
                version_id = version_id or chunk.document_version_id
                page_number = page_number if page_number is not None else chunk.page_number
                section = section if section is not None else chunk.section
        document_version_no: int | None = None
        document_title: str | None = None
        if version_id is not None:
            version = await session.get(DocumentVersion, version_id)
            if version is not None:
                document_version_no = version.version_no
                document_id = document_id or version.document_id
        if document_id is not None:
            document = await session.get(Document, document_id)
            if document is not None:
                document_title = document.title
        resolved.append(
            EvidenceItem(
                agent=str(raw.get("agent", "")),
                evidence_id=str(raw.get("evidence_id", "")),
                kind=str(raw.get("kind", "")),
                description=str(raw.get("description", "")),
                record_type=raw.get("record_type"),
                record_id=_uuid(raw.get("record_id")),
                record_version=raw.get("record_version"),
                document_id=document_id,
                document_title=document_title,
                document_version_no=document_version_no,
                page_number=page_number,
                section=section,
                chunk_id=chunk_id,
                citation_url=(
                    CITATION_URL_TEMPLATE.format(chunk_id=chunk_id)
                    if chunk_id is not None
                    else None
                ),
            )
        )
    return resolved


async def _slot_diffs(session: AsyncSession, rec: Recommendation) -> list[SlotDiff]:
    diffs: list[SlotDiff] = []
    for row in _rows(rec.proposal, "allocations"):
        slot_id = _uuid(row.get("slot_id"))
        if slot_id is None:
            continue
        slot = await session.get(LineCapacitySlot, slot_id)
        if slot is None:
            continue
        line = await session.get(Line, slot.line_id)
        standard_minutes = _decimal(row.get("standard_minutes"))
        capacity = slot.available_operator_minutes * slot.planned_efficiency
        allocated_after = slot.allocated_standard_minutes + standard_minutes
        diffs.append(
            SlotDiff(
                slot_id=slot.id,
                line_code=line.code if line is not None else str(row.get("line_code", "?")),
                slot_date=slot.slot_date,
                shift_code=slot.shift_code,
                capacity=capacity,
                allocated_before=slot.allocated_standard_minutes,
                allocated_after=allocated_after,
                remaining_after=max(Decimal(0), capacity - allocated_after),
                utilization_after=utilization(allocated_after, capacity),
                standard_minutes=standard_minutes,
                units=_decimal(row.get("units")),
            )
        )
    return diffs


async def _reservation_diffs(session: AsyncSession, rec: Recommendation) -> list[ReservationDiff]:
    diffs: list[ReservationDiff] = []
    for row in _rows(rec.proposal, "reservations"):
        material_id = _uuid(row.get("material_id"))
        if material_id is None:
            continue
        material = await session.get(Material, material_id)
        balance = await session.scalar(
            select(MaterialBalance).where(
                MaterialBalance.factory_id == rec.factory_id,
                MaterialBalance.material_id == material_id,
            )
        )
        quantity = _decimal(row.get("quantity"))
        on_hand = balance.on_hand_accepted if balance is not None else Decimal(0)
        reserved_before = balance.reserved if balance is not None else Decimal(0)
        reserved_after = reserved_before + quantity
        diffs.append(
            ReservationDiff(
                material_id=material_id,
                material_code=(
                    material.code if material is not None else str(row.get("material_code", "?"))
                ),
                quantity=quantity,
                unit=(material.unit if material is not None else str(row.get("unit", ""))),
                on_hand=on_hand,
                reserved_before=reserved_before,
                reserved_after=reserved_after,
                available_after=available_now(on_hand, reserved_after),
            )
        )
    return diffs


def _decide_block(rec: Recommendation, principal: Principal, *, expired: bool) -> str | None:
    if not principal.has(DECIDE_PERMISSION, rec.factory_id):
        return BLOCKED_MISSING_PERMISSION
    if rec.status != RecommendationStatus.PROPOSED.value:
        return BLOCKED_WRONG_STATUS
    if expired:
        return BLOCKED_EXPIRED
    if principal.user_id == rec.proposer_user_id:
        return BLOCKED_SELF_APPROVAL
    return None


def _apply_block(
    rec: Recommendation, principal: Principal, *, expired: bool, stale: bool
) -> str | None:
    if not principal.has(APPLY_PERMISSION, rec.factory_id):
        return BLOCKED_MISSING_PERMISSION
    if rec.status != RecommendationStatus.APPROVED.value:
        return BLOCKED_WRONG_STATUS
    if expired:
        return BLOCKED_EXPIRED
    if principal.user_id == rec.proposer_user_id:
        return BLOCKED_SELF_APPROVAL
    if stale:
        return BLOCKED_STALE
    return None


async def recommendation_detail(
    session: AsyncSession, principal: Principal, rec_id: uuid.UUID
) -> RecommendationDetail:
    """Everything the review screen shows, including what the caller may do."""
    rec = await load_scoped(session, Recommendation, rec_id, principal, READ_PERMISSION)
    order = await session.get(Order, rec.order_id)
    run = await session.get(AnalysisRun, rec.run_id)
    if order is None or run is None:
        raise RuntimeError(f"recommendation {rec.id} references a missing order or run")
    decision = await decision_ref(session, rec)
    stale_inputs = await check_staleness(session, rec)
    expired = _is_expired(rec)
    return RecommendationDetail(
        recommendation=rec,
        order=order,
        run=RunRef(
            id=run.id,
            status=run.status,
            llm_provider=run.llm_provider,
            llm_model=run.llm_model,
        ),
        proposer=await _user_ref(session, rec.proposer_user_id),
        decision=decision,
        evidence=await _evidence(session, rec),
        slots=await _slot_diffs(session, rec),
        reservations=await _reservation_diffs(session, rec),
        stale=bool(stale_inputs),
        stale_inputs=stale_inputs,
        expired=expired,
        can_decide=_decide_block(rec, principal, expired=expired) is None,
        decide_blocked_reason=_decide_block(rec, principal, expired=expired),
        can_apply=_apply_block(rec, principal, expired=expired, stale=bool(stale_inputs)) is None,
        apply_blocked_reason=_apply_block(
            rec, principal, expired=expired, stale=bool(stale_inputs)
        ),
        status_source=_status_source(rec, decision),
    )


# --------------------------------------------------------------------------
# Shared gate for decide/apply
# --------------------------------------------------------------------------


async def _expire(
    session: AsyncSession,
    rec: Recommendation,
    principal: Principal,
    *,
    action: str,
    trace_id: str | None,
) -> PersistedRejection:
    """Mark ``rec`` EXPIRED, audit and notify; the caller commits and raises."""
    before = {"status": rec.status}
    rec.status = RecommendationStatus.EXPIRED.value
    rec.version += 1
    await session.flush()
    await _audit(
        session,
        rec,
        principal,
        action=action,
        outcome=AuditOutcome.FAILED.value,
        reason="EXPIRED",
        trace_id=trace_id,
        before=before,
        after={"status": rec.status},
    )
    await _notify(
        session,
        rec,
        user_id=rec.proposer_user_id,
        role=None,
        kind="recommendation.expired",
        title="A proposal expired before it was decided",
        body="The proposal expired — run a new analysis.",
    )
    return PersistedRejection(409, "EXPIRED", "This proposal has expired.")


def _self_approval_error() -> AppError:
    return AppError(
        403,
        "SELF_APPROVAL_DENIED",
        "The person who requested an analysis cannot decide or apply its proposal.",
    )


def _hash_error() -> AppError:
    return AppError(
        409,
        "CONFLICT",
        "Proposal changed",
        field_errors=[
            {"field": "proposal_hash", "message": "Reload the proposal and review it again."}
        ],
    )


# --------------------------------------------------------------------------
# decide
# --------------------------------------------------------------------------


async def decide(
    session: AsyncSession,
    principal: Principal,
    rec_id: uuid.UUID,
    *,
    decision: str,
    reason: str | None,
    proposal_hash: str,
    trace_id: str | None = None,
) -> Recommendation:
    """Record an APPROVED/REJECTED decision. Approving does **not** apply."""
    scoped = await load_scoped(session, Recommendation, rec_id, principal, DECIDE_PERMISSION)
    await _lock_order(session, scoped.order_id)
    rec = await _lock_recommendation(session, scoped.id)

    if rec.status != RecommendationStatus.PROPOSED.value:
        raise AppError(409, "CONFLICT", f"A {rec.status} proposal cannot be decided.")
    if _is_expired(rec):
        raise await _expire(session, rec, principal, action=DECIDE_ACTION, trace_id=trace_id)
    if principal.user_id == rec.proposer_user_id:
        raise _self_approval_error()
    if proposal_hash != rec.proposal_hash:
        raise _hash_error()

    try:
        resolved = ApprovalDecision(decision)
    except ValueError as exc:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"Unknown decision {decision!r}.",
            field_errors=[{"field": "decision", "message": "Must be APPROVED or REJECTED."}],
        ) from exc
    text = (reason or "").strip()
    if resolved is ApprovalDecision.REJECTED and not (
        REASON_MIN_LENGTH <= len(text) <= REASON_MAX_LENGTH
    ):
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "A rejection needs a reason.",
            field_errors=[
                {
                    "field": "reason",
                    "message": (f"Must be {REASON_MIN_LENGTH}-{REASON_MAX_LENGTH} characters."),
                }
            ],
        )

    decided_at = utcnow()
    session.add(
        Approval(
            recommendation_id=rec.id,
            decision=resolved.value,
            decided_by=principal.user_id,
            reason=text,
            proposal_hash=rec.proposal_hash,
            decided_at=decided_at,
        )
    )
    before = {"status": rec.status}
    rec.status = (
        RecommendationStatus.APPROVED.value
        if resolved is ApprovalDecision.APPROVED
        else RecommendationStatus.REJECTED.value
    )
    rec.version += 1
    await session.flush()

    await _audit(
        session,
        rec,
        principal,
        action=DECIDE_ACTION,
        outcome=AuditOutcome.SUCCESS.value,
        reason=text or None,
        trace_id=trace_id,
        before=before,
        after={
            "status": rec.status,
            "decision": resolved.value,
            "decided_by": str(principal.user_id),
        },
    )
    await _notify(
        session,
        rec,
        user_id=rec.proposer_user_id,
        role=None,
        kind="recommendation.decided",
        title=f"Your proposal was {resolved.value.lower()}",
        body=(
            f"{principal.display_name} {resolved.value.lower()} the proposal"
            + (f": {text}" if text else ".")
        ),
    )
    return rec


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


async def _lock_inputs(
    session: AsyncSession, order: Order, rec: Recommendation
) -> tuple[dict[uuid.UUID, LineCapacitySlot], set[uuid.UUID]]:
    """Lock every slot and balance the transaction may touch, in the mandated
    order (slots by ascending id, then balances by ascending id).

    Returns the locked slots and the material ids whose balances were locked,
    so the later `release_*`/`allocate`/`reserve_material` calls never need a
    lock this transaction does not already hold.
    """
    slot_ids: set[uuid.UUID] = set()
    for row in _rows(rec.proposal, "allocations"):
        slot_id = _uuid(row.get("slot_id"))
        if slot_id is not None:
            slot_ids.add(slot_id)
    # Every slot the order ever used: `release_order_allocations` locks that
    # same set, so it must be inside ours.
    slot_ids.update(
        (
            await session.scalars(
                select(Allocation.slot_id).where(Allocation.order_id == order.id).distinct()
            )
        ).all()
    )
    slots = await lock_slots(session, slot_ids)

    material_ids: set[uuid.UUID] = set()
    for row in _rows(rec.proposal, "reservations"):
        material_id = _uuid(row.get("material_id"))
        if material_id is not None:
            material_ids.add(material_id)
    # `release_order_reservations` locks the BOM's balances and those of every
    # reservation the order ever had.
    material_ids.update(
        (
            await session.scalars(
                select(BomLine.material_id).where(BomLine.bom_version_id == order.bom_version_id)
            )
        ).all()
    )
    material_ids.update(
        (
            await session.scalars(
                select(Reservation.material_id).where(Reservation.order_id == order.id)
            )
        ).all()
    )
    # A proposed material without a balance row yet must get one *before* the
    # lock sweep, or `reserve_material` would create and lock it out of order.
    for material_id in sorted(material_ids, key=str):
        await ensure_balance(
            session,
            organization_id=order.organization_id,
            factory_id=order.factory_id,
            material_id=material_id,
        )
    balance_ids = (
        await session.scalars(
            select(MaterialBalance.id).where(
                MaterialBalance.factory_id == order.factory_id,
                MaterialBalance.material_id.in_(sorted(material_ids, key=str)),
            )
        )
    ).all()
    await lock_balances(session, balance_ids)
    return slots, material_ids


async def _supersede(
    session: AsyncSession,
    rec: Recommendation,
    principal: Principal,
    stale_inputs: Sequence[StaleInput],
    *,
    trace_id: str | None,
) -> PersistedRejection:
    """Mark ``rec`` SUPERSEDED for stale inputs; the caller commits and raises."""
    before = {"status": rec.status}
    rec.status = RecommendationStatus.SUPERSEDED.value
    rec.superseded_reason = _stale_reason(stale_inputs)
    rec.version += 1
    await session.flush()
    await _audit(
        session,
        rec,
        principal,
        action=APPLY_ACTION,
        outcome=AuditOutcome.FAILED.value,
        reason=rec.superseded_reason,
        trace_id=trace_id,
        before=before,
        after={
            "status": rec.status,
            "stale_inputs": [
                {
                    "kind": item.kind,
                    "id": str(item.id),
                    "expected_version": item.expected_version,
                    "current_version": item.current_version,
                }
                for item in stale_inputs
            ],
        },
    )
    await _notify(
        session,
        rec,
        user_id=rec.proposer_user_id,
        role=None,
        kind="recommendation.superseded",
        title="A proposal became out of date",
        body=STALE_NOTIFICATION_BODY,
    )
    return PersistedRejection(
        409,
        "STALE_INPUT",
        "The inputs this proposal was computed from have changed.",
        field_errors=_stale_field_errors(stale_inputs),
    )


def _allocation_summary(allocations: Iterable[Allocation]) -> list[dict[str, str]]:
    return [
        {
            "slot_id": str(row.slot_id),
            "standard_minutes": str(row.standard_minutes),
            "units": str(row.units),
        }
        for row in allocations
    ]


def _reservation_summary(reservations: Iterable[Reservation]) -> list[dict[str, str]]:
    return [
        {"material_id": str(row.material_id), "quantity": str(row.quantity)} for row in reservations
    ]


async def apply(
    session: AsyncSession,
    principal: Principal,
    rec_id: uuid.UUID,
    *,
    proposal_hash: str,
    trace_id: str | None = None,
) -> ApplyResult:
    """Apply an approved proposal: release what the order held, allocate and
    reserve exactly what the proposal says, and plan the order.

    Runs entirely in the caller's transaction. The caller retries the whole
    transaction on a serialization failure or deadlock, commits on success,
    and commits-then-raises a `PersistedRejection`.
    """
    scoped = await load_scoped(session, Recommendation, rec_id, principal, APPLY_PERMISSION)
    order = await _lock_order(session, scoped.order_id)
    rec = await _lock_recommendation(session, scoped.id)

    if rec.status != RecommendationStatus.APPROVED.value:
        raise AppError(409, "CONFLICT", f"A {rec.status} proposal cannot be applied.")
    if _is_expired(rec):
        raise await _expire(session, rec, principal, action=APPLY_ACTION, trace_id=trace_id)
    if proposal_hash != rec.proposal_hash:
        raise _hash_error()
    if principal.user_id == rec.proposer_user_id:
        raise _self_approval_error()

    slots, material_ids = await _lock_inputs(session, order, rec)

    stale_inputs = await check_staleness(session, rec)
    if stale_inputs:
        raise await _supersede(session, rec, principal, stale_inputs, trace_id=trace_id)

    if order.production_state not in APPLICABLE_ORDER_STATES:
        raise AppError(
            409,
            "INVALID_TRANSITION",
            f"A {order.production_state} order cannot be planned from a proposal.",
        )

    order_before = {
        "production_state": order.production_state,
        "material_state": order.material_state,
        "version": order.version,
    }
    released_allocations = await release_order_allocations(session, order)
    released_reservations = await release_order_reservations(session, order)
    before: dict[str, Any] = {
        "order": order_before,
        "allocations": _allocation_summary(released_allocations),
        "reservations": _reservation_summary(released_reservations),
    }

    created_allocations: list[AppliedAllocation] = []
    for row in _rows(rec.proposal, "allocations"):
        slot_id = _uuid(row.get("slot_id"))
        if slot_id is None or slot_id not in slots:
            raise AppError(409, "CONFLICT", "The proposal references an unknown capacity slot.")
        allocation = await allocate(
            session,
            slot_id=slot_id,
            order_id=order.id,
            standard_minutes=_decimal(row.get("standard_minutes")),
            units=_decimal(row.get("units")),
            actor_user_id=principal.user_id,
            recommendation_id=rec.id,
        )
        created_allocations.append(
            AppliedAllocation(
                id=allocation.id,
                slot_id=allocation.slot_id,
                standard_minutes=allocation.standard_minutes,
                units=allocation.units,
            )
        )

    created_reservations: list[AppliedReservation] = []
    for row in _rows(rec.proposal, "reservations"):
        material_id = _uuid(row.get("material_id"))
        if material_id is None:
            raise AppError(409, "CONFLICT", "The proposal references an unknown material.")
        reservation = await reserve_material(
            session,
            organization_id=order.organization_id,
            factory_id=order.factory_id,
            material_id=material_id,
            order_id=order.id,
            quantity=_decimal(row.get("quantity")),
            actor_user_id=principal.user_id,
            recommendation_id=rec.id,
        )
        created_reservations.append(
            AppliedReservation(
                id=reservation.id,
                material_id=reservation.material_id,
                quantity=reservation.quantity,
            )
        )

    order.production_state = ProductionState.PLANNED.value
    order.version += 1
    await session.flush()
    # Only this order: its row is the only order row this transaction holds.
    await recompute_material_states(session, order.factory_id, material_ids, order_ids=[order.id])

    applied_at = utcnow()
    rec.status = RecommendationStatus.APPLIED.value
    rec.applied_by = principal.user_id
    rec.applied_at = applied_at
    rec.version += 1
    await session.flush()

    after = {
        "order": {
            "production_state": order.production_state,
            "material_state": order.material_state,
            "version": order.version,
        },
        "allocations": [
            {
                "slot_id": str(row.slot_id),
                "standard_minutes": str(row.standard_minutes),
                "units": str(row.units),
            }
            for row in created_allocations
        ],
        "reservations": [
            {"material_id": str(row.material_id), "quantity": str(row.quantity)}
            for row in created_reservations
        ],
    }
    await _audit(
        session,
        rec,
        principal,
        action=APPLY_ACTION,
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=trace_id,
        before=before,
        after=after,
    )
    await _notify(
        session,
        rec,
        user_id=rec.proposer_user_id,
        role=None,
        kind="recommendation.applied",
        title=f"Your proposal for {order.external_ref} was applied",
        body=f"{principal.display_name} applied the proposal; the order is now PLANNED.",
    )
    await _notify(
        session,
        rec,
        user_id=None,
        role=Role.PLANNER.value,
        kind="recommendation.applied",
        title=f"Order {order.external_ref} is planned",
        body=(
            f"{principal.display_name} applied an approved proposal for order {order.external_ref}."
        ),
    )
    if material_ids:
        await enqueue(
            session,
            queue="maintenance",
            job_type=REFRESH_MATERIAL_STATES_JOB,
            payload={
                "factory_id": str(order.factory_id),
                "material_ids": sorted(str(material_id) for material_id in material_ids),
            },
            dedupe_key=f"refresh:{order.id}:{rec.id}",
        )

    return ApplyResult(
        recommendation_id=rec.id,
        status=rec.status,
        applied_at=applied_at,
        order=order,
        allocations=created_allocations,
        reservations=created_reservations,
        released_allocations=len(released_allocations),
        released_reservations=len(released_reservations),
    )


__all__ = [
    "APPLY_ACTION",
    "APPLY_PERMISSION",
    "DECIDE_ACTION",
    "DECIDE_PERMISSION",
    "READ_PERMISSION",
    "ApplyResult",
    "PersistedRejection",
    "RecommendationDetail",
    "RecommendationRow",
    "StaleInput",
    "apply",
    "check_staleness",
    "decide",
    "list_recommendations",
    "recommendation_detail",
]
