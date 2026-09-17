"""Capacity queries and commands (task-8-brief.md).

Lock order (shared with `app.domain.inventory.service` and the approval
apply path): orders -> `line_capacity_slots` -> `material_balances`, each
group locked with ``SELECT ... FOR UPDATE`` in ascending id order.

Every write happens inside the caller's transaction; nothing here commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import record_audit
from app.db.models import (
    Allocation,
    Line,
    LineCapability,
    LineCapacitySlot,
    Order,
    StyleOperation,
)
from app.domain.planning.calc import SlotCapacity, utilization
from app.domain.vocab import ActorType, AllocationStatus, AuditOutcome

MAX_BOARD_DAYS = 31
_MINUTES = Decimal("0.01")
_UNITS = Decimal("0.0001")


@dataclass(frozen=True)
class BoardAllocation:
    id: uuid.UUID
    order_id: uuid.UUID
    order_external_ref: str
    standard_minutes: Decimal
    units: Decimal


@dataclass(frozen=True)
class BoardSlot:
    id: uuid.UUID
    slot_date: date
    shift_code: str
    available_operator_minutes: Decimal
    planned_efficiency: Decimal
    capacity_standard_minutes: Decimal
    allocated_standard_minutes: Decimal
    remaining_standard_minutes: Decimal
    utilization: Decimal | None
    version: int
    allocations: tuple[BoardAllocation, ...]


@dataclass(frozen=True)
class BoardLine:
    id: uuid.UUID
    code: str
    name: str
    operator_count: int
    is_active: bool
    slots: tuple[BoardSlot, ...]


@dataclass(frozen=True)
class CapacityBoard:
    factory_id: uuid.UUID
    start: date
    end: date
    lines: tuple[BoardLine, ...]


def _to_capacity(slot: LineCapacitySlot) -> SlotCapacity:
    return SlotCapacity(
        slot_id=slot.id,
        line_id=slot.line_id,
        slot_date=slot.slot_date,
        shift_code=slot.shift_code,
        available_operator_minutes=slot.available_operator_minutes,
        planned_efficiency=slot.planned_efficiency,
        allocated_standard_minutes=slot.allocated_standard_minutes,
    )


async def lock_slots(
    session: AsyncSession, slot_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, LineCapacitySlot]:
    """Lock ``slot_ids`` (``FOR UPDATE``, ascending id) and return fresh rows."""
    ids = sorted(set(slot_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(LineCapacitySlot)
            .where(LineCapacitySlot.id.in_(ids))
            .order_by(LineCapacitySlot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    return {row.id: row for row in rows}


async def compatible_line_ids(
    session: AsyncSession, factory_id: uuid.UUID, style_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """Active lines of ``factory_id`` holding every skill the style's operations need."""
    required = set(
        (
            await session.scalars(
                select(StyleOperation.skill_code).where(StyleOperation.style_id == style_id)
            )
        ).all()
    )
    lines = (
        await session.scalars(
            select(Line.id).where(Line.factory_id == factory_id, Line.is_active.is_(True))
        )
    ).all()
    if not lines:
        return frozenset()
    capabilities: dict[uuid.UUID, set[str]] = {line_id: set() for line_id in lines}
    rows = (
        await session.execute(
            select(LineCapability.line_id, LineCapability.skill_code).where(
                LineCapability.line_id.in_(list(lines))
            )
        )
    ).all()
    for line_id, skill_code in rows:
        capabilities[line_id].add(skill_code)
    return frozenset(line_id for line_id, skills in capabilities.items() if required <= skills)


async def slot_capacities(
    session: AsyncSession,
    factory_id: uuid.UUID,
    *,
    start: date,
    end: date,
    line_ids: Iterable[uuid.UUID] | None = None,
) -> list[SlotCapacity]:
    """Slots of ``factory_id`` dated within ``[start, end]``, in planning order."""
    stmt = select(LineCapacitySlot).where(
        LineCapacitySlot.factory_id == factory_id,
        LineCapacitySlot.slot_date >= start,
        LineCapacitySlot.slot_date <= end,
    )
    if line_ids is not None:
        stmt = stmt.where(LineCapacitySlot.line_id.in_(list(line_ids)))
    rows = (
        await session.scalars(
            stmt.order_by(
                LineCapacitySlot.slot_date, LineCapacitySlot.shift_code, LineCapacitySlot.line_id
            )
        )
    ).all()
    return [_to_capacity(row) for row in rows]


async def allocate(
    session: AsyncSession,
    *,
    slot_id: uuid.UUID,
    order_id: uuid.UUID,
    standard_minutes: Decimal,
    units: Decimal,
    actor_user_id: uuid.UUID | None,
    recommendation_id: uuid.UUID | None,
) -> Allocation:
    """Allocate ``standard_minutes`` of a slot to an order.

    The caller must already hold the slot's row lock (`lock_slots`) in this
    transaction; the capacity check below is only race-free under that lock.
    Raises 409 ``CONFLICT`` when the slot's remaining capacity is too small.
    """
    # Store exactly what the numeric(12,2)/numeric(14,4) columns will hold
    # (PostgreSQL rounds half away from zero), so the slot total and the
    # allocation rows never drift apart.
    standard_minutes = standard_minutes.quantize(_MINUTES, rounding=ROUND_HALF_UP)
    units = units.quantize(_UNITS, rounding=ROUND_HALF_UP)
    if standard_minutes <= 0 or units <= 0:
        raise AppError(422, "VALIDATION_ERROR", "Allocation minutes and units must be positive.")
    slot = await session.get(LineCapacitySlot, slot_id)
    if slot is None:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    order = await session.get(Order, order_id)
    if order is None or order.factory_id != slot.factory_id:
        raise AppError(422, "VALIDATION_ERROR", "Order and capacity slot must share a factory.")

    capacity = slot.available_operator_minutes * slot.planned_efficiency
    if slot.allocated_standard_minutes + standard_minutes > capacity:
        raise AppError(
            409,
            "CONFLICT",
            "Insufficient remaining capacity",
            field_errors=[
                {
                    "field": "standard_minutes",
                    "message": (
                        f"Slot {slot.id} has "
                        f"{max(Decimal(0), capacity - slot.allocated_standard_minutes)} "
                        "standard minutes remaining."
                    ),
                }
            ],
        )

    before = {
        "allocated_standard_minutes": str(slot.allocated_standard_minutes),
        "version": slot.version,
    }
    allocation = Allocation(
        organization_id=slot.organization_id,
        factory_id=slot.factory_id,
        order_id=order.id,
        slot_id=slot.id,
        standard_minutes=standard_minutes,
        units=units,
        status=AllocationStatus.ACTIVE.value,
        recommendation_id=recommendation_id,
        created_by=actor_user_id,
    )
    session.add(allocation)
    slot.allocated_standard_minutes += standard_minutes
    slot.version += 1
    await session.flush()

    await record_audit(
        session,
        organization_id=slot.organization_id,
        factory_id=slot.factory_id,
        actor_type=(ActorType.USER if actor_user_id is not None else ActorType.SYSTEM).value,
        actor_id=str(actor_user_id) if actor_user_id is not None else "system",
        action="capacity.allocate",
        target_type="allocation",
        target_id=str(allocation.id),
        outcome=AuditOutcome.SUCCESS.value,
        before={"slot_id": str(slot.id), **before},
        after={
            "slot_id": str(slot.id),
            "order_id": str(order.id),
            "standard_minutes": str(standard_minutes),
            "units": str(units),
            "allocated_standard_minutes": str(slot.allocated_standard_minutes),
            "version": slot.version,
        },
    )
    return allocation


async def release_order_allocations(session: AsyncSession, order: Order) -> list[Allocation]:
    """Release every ACTIVE allocation of ``order`` under slot row locks.

    Returns the released allocations. The caller owns auditing of the
    command that triggered the release (e.g. the order cancellation).
    """
    allocations = list(
        (
            await session.scalars(
                select(Allocation)
                .where(
                    Allocation.order_id == order.id,
                    Allocation.status == AllocationStatus.ACTIVE.value,
                )
                .order_by(Allocation.id)
            )
        ).all()
    )
    if not allocations:
        return []
    slots = await lock_slots(session, (allocation.slot_id for allocation in allocations))
    for allocation in allocations:
        slot = slots[allocation.slot_id]
        slot.allocated_standard_minutes -= allocation.standard_minutes
        slot.version += 1
        allocation.status = AllocationStatus.RELEASED.value
    await session.flush()
    return allocations


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "The end date must not be before the start date.",
            field_errors=[{"field": "end", "message": "Must be on or after start."}],
        )
    if (end - start).days + 1 > MAX_BOARD_DAYS:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"The date range may span at most {MAX_BOARD_DAYS} days.",
            field_errors=[
                {"field": "end", "message": f"At most {MAX_BOARD_DAYS} days after start."}
            ],
        )


async def capacity_board(
    session: AsyncSession, factory_id: uuid.UUID, start: date, end: date
) -> CapacityBoard:
    """Per line, per slot in ``[start, end]`` (at most 31 days): capacity,
    allocation, remaining minutes, utilization, version and ACTIVE
    allocations with their order references."""
    _validate_range(start, end)
    lines = (
        await session.scalars(select(Line).where(Line.factory_id == factory_id).order_by(Line.code))
    ).all()
    slots = (
        await session.scalars(
            select(LineCapacitySlot)
            .where(
                LineCapacitySlot.factory_id == factory_id,
                LineCapacitySlot.slot_date >= start,
                LineCapacitySlot.slot_date <= end,
            )
            .order_by(LineCapacitySlot.slot_date, LineCapacitySlot.shift_code)
        )
    ).all()
    allocations_by_slot: dict[uuid.UUID, list[BoardAllocation]] = {}
    if slots:
        rows = (
            await session.execute(
                select(Allocation, Order.external_ref)
                .join(Order, Order.id == Allocation.order_id)
                .where(
                    Allocation.slot_id.in_([slot.id for slot in slots]),
                    Allocation.status == AllocationStatus.ACTIVE.value,
                )
                .order_by(Allocation.created_at, Allocation.id)
            )
        ).all()
        for allocation, external_ref in rows:
            allocations_by_slot.setdefault(allocation.slot_id, []).append(
                BoardAllocation(
                    id=allocation.id,
                    order_id=allocation.order_id,
                    order_external_ref=external_ref,
                    standard_minutes=allocation.standard_minutes,
                    units=allocation.units,
                )
            )

    slots_by_line: dict[uuid.UUID, list[BoardSlot]] = {}
    for slot in slots:
        capacity = _to_capacity(slot)
        slots_by_line.setdefault(slot.line_id, []).append(
            BoardSlot(
                id=slot.id,
                slot_date=slot.slot_date,
                shift_code=slot.shift_code,
                available_operator_minutes=slot.available_operator_minutes,
                planned_efficiency=slot.planned_efficiency,
                capacity_standard_minutes=capacity.capacity_standard_minutes,
                allocated_standard_minutes=slot.allocated_standard_minutes,
                remaining_standard_minutes=capacity.remaining_standard_minutes,
                utilization=utilization(
                    slot.allocated_standard_minutes, capacity.capacity_standard_minutes
                ),
                version=slot.version,
                allocations=tuple(allocations_by_slot.get(slot.id, ())),
            )
        )

    return CapacityBoard(
        factory_id=factory_id,
        start=start,
        end=end,
        lines=tuple(
            BoardLine(
                id=line.id,
                code=line.code,
                name=line.name,
                operator_count=line.operator_count,
                is_active=line.is_active,
                slots=tuple(slots_by_line.get(line.id, ())),
            )
            for line in lines
        ),
    )


async def list_lines(
    session: AsyncSession, factory_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[tuple[Line, list[str]]], int]:
    """Lines of ``factory_id`` (by code) with their sorted skill codes."""
    base = select(Line).where(Line.factory_id == factory_id)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    lines = list(
        (await session.scalars(base.order_by(Line.code).limit(limit).offset(offset))).all()
    )
    skills: dict[uuid.UUID, list[str]] = {line.id: [] for line in lines}
    if lines:
        rows = (
            await session.execute(
                select(LineCapability.line_id, LineCapability.skill_code)
                .where(LineCapability.line_id.in_(list(skills)))
                .order_by(LineCapability.skill_code)
            )
        ).all()
        for line_id, skill_code in rows:
            skills[line_id].append(skill_code)
    return [(line, skills[line.id]) for line in lines], int(total or 0)
