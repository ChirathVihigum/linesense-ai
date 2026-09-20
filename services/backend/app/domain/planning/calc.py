"""Planning: standard minutes, capacity, utilization, and earliest-slot
allocation.

Pure functions and immutable dataclasses; no database access or I/O. See
`docs/architecture/formulas.md` for the underlying formulas and the
plan's reference fixtures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

_NO_COMPATIBLE_LINE = "NO_COMPATIBLE_LINE"
_INSUFFICIENT_CAPACITY_BEFORE_DUE_DATE = "INSUFFICIENT_CAPACITY_BEFORE_DUE_DATE"
_LIMITED_BY_MATERIAL = "LIMITED_BY_MATERIAL"

_COMPARISON_PLACES = Decimal("0.000001")

# `line_capacity_slots.allocated_standard_minutes` is `numeric(12,2)`, so a
# remainder below 0.01 standard minutes cannot be booked against a slot:
# `app.domain.capacity.service.allocate` stores the rounded value and would
# then exceed the slot's capacity. Capacity itself is not 2dp (operator
# minutes x planned efficiency easily yields six decimals), so the free
# remainder is floored to the storable precision — otherwise a plan proposes
# slivers that can never be applied.
ALLOCATABLE_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class SlotCapacity:
    slot_id: UUID
    line_id: UUID
    slot_date: date
    shift_code: str
    available_operator_minutes: Decimal
    planned_efficiency: Decimal
    allocated_standard_minutes: Decimal

    @property
    def capacity_standard_minutes(self) -> Decimal:
        return self.available_operator_minutes * self.planned_efficiency

    @property
    def remaining_standard_minutes(self) -> Decimal:
        """Free capacity, floored to what a slot can actually hold (2dp)."""
        free = max(Decimal(0), self.capacity_standard_minutes - self.allocated_standard_minutes)
        return free.quantize(ALLOCATABLE_PLACES, rounding=ROUND_DOWN)


@dataclass(frozen=True)
class SlotAllocation:
    slot_id: UUID
    line_id: UUID
    slot_date: date
    shift_code: str
    standard_minutes: Decimal
    units: Decimal


@dataclass(frozen=True)
class AllocationPlan:
    allocations: tuple[SlotAllocation, ...]
    requested_units: int
    allocated_units: Decimal
    unscheduled_units: Decimal
    unscheduled_reason: str | None
    finish_date: date | None
    required_standard_minutes: Decimal


def required_standard_minutes(remaining_units: int, sam_minutes_per_unit: Decimal) -> Decimal:
    """`remaining_units * SAM_minutes_per_unit`."""
    if remaining_units < 0:
        raise ValueError("remaining_units must be >= 0")
    if sam_minutes_per_unit <= 0:
        raise ValueError("sam_minutes_per_unit must be positive")
    return Decimal(remaining_units) * sam_minutes_per_unit


def available_standard_minutes(
    operator_minutes: Sequence[Decimal], planned_efficiency: Decimal
) -> Decimal:
    """`sum(operator_minutes) * planned_efficiency`.

    Efficiency is applied exactly once here; it must never be compounded
    with a second efficiency factor elsewhere.
    """
    if not (Decimal("0") < planned_efficiency <= Decimal("1")):
        raise ValueError("planned_efficiency must be in (0, 1]")
    return sum(operator_minutes, Decimal(0)) * planned_efficiency


def utilization(
    allocated_standard_minutes: Decimal, available_standard_minutes: Decimal
) -> Decimal | None:
    """`allocated / available`, or `None` when available capacity is zero."""
    if available_standard_minutes == 0:
        return None
    return allocated_standard_minutes / available_standard_minutes


def plan_earliest_slots(
    *,
    units: int,
    sam_minutes_per_unit: Decimal,
    slots: Sequence[SlotCapacity],
    earliest_date: date,
    due_date: date,
    compatible_line_ids: frozenset[UUID],
    max_units: Decimal | None = None,
) -> AllocationPlan:
    """Greedily allocate `units` to the earliest usable capacity slots.

    Slots are considered when `earliest_date <= slot_date <= due_date`,
    the slot's line is in `compatible_line_ids`, and the slot has
    remaining standard minutes. Eligible slots are filled in
    `(slot_date, shift_code, str(line_id))` order (the documented
    tie-break). `target_units = min(units, max_units)` when `max_units`
    is given, so material limits are never exceeded by the plan itself.
    """
    if units < 0:
        raise ValueError("units must be >= 0")
    if sam_minutes_per_unit <= 0:
        raise ValueError("sam_minutes_per_unit must be positive")

    total_units = Decimal(units)
    total_required_minutes = required_standard_minutes(units, sam_minutes_per_unit)
    target_units = total_units if max_units is None else min(total_units, max_units)

    if not any(slot.line_id in compatible_line_ids for slot in slots):
        return AllocationPlan(
            allocations=(),
            requested_units=units,
            allocated_units=Decimal(0),
            unscheduled_units=total_units,
            unscheduled_reason=_NO_COMPATIBLE_LINE,
            finish_date=None,
            required_standard_minutes=total_required_minutes,
        )

    eligible = [
        slot
        for slot in slots
        if slot.line_id in compatible_line_ids
        and earliest_date <= slot.slot_date <= due_date
        and slot.remaining_standard_minutes > 0
    ]
    eligible.sort(key=lambda slot: (slot.slot_date, slot.shift_code, str(slot.line_id)))

    allocations: list[SlotAllocation] = []
    remaining_target = target_units
    for slot in eligible:
        if remaining_target <= 0:
            break
        required_minutes_for_remaining = remaining_target * sam_minutes_per_unit
        take_minutes = min(required_minutes_for_remaining, slot.remaining_standard_minutes)
        if take_minutes <= 0:
            continue
        take_units = take_minutes / sam_minutes_per_unit
        allocations.append(
            SlotAllocation(
                slot_id=slot.slot_id,
                line_id=slot.line_id,
                slot_date=slot.slot_date,
                shift_code=slot.shift_code,
                standard_minutes=take_minutes,
                units=take_units,
            )
        )
        remaining_target -= take_units

    allocated_units = sum((allocation.units for allocation in allocations), Decimal(0))
    unscheduled_units = total_units - allocated_units

    allocated_q = allocated_units.quantize(_COMPARISON_PLACES)
    target_q = target_units.quantize(_COMPARISON_PLACES)
    total_q = total_units.quantize(_COMPARISON_PLACES)

    reason: str | None
    if allocated_q >= target_q:
        reason = None if target_q >= total_q else _LIMITED_BY_MATERIAL
    else:
        reason = _INSUFFICIENT_CAPACITY_BEFORE_DUE_DATE

    finish_date = allocations[-1].slot_date if allocations else None

    return AllocationPlan(
        allocations=tuple(allocations),
        requested_units=units,
        allocated_units=allocated_units,
        unscheduled_units=unscheduled_units,
        unscheduled_reason=reason,
        finish_date=finish_date,
        required_standard_minutes=total_required_minutes,
    )
