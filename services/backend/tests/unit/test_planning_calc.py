"""Unit tests for `app.domain.planning.calc` beyond the reference fixtures."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest

from app.domain.planning.calc import (
    SlotCapacity,
    available_standard_minutes,
    plan_earliest_slots,
    required_standard_minutes,
    utilization,
)

LINE_A = uuid4()
LINE_B = uuid4()


def _slot(
    *,
    line_id: UUID,
    day: date,
    shift: str = "A",
    available_minutes: D = D("480"),
    efficiency: D = D("1"),
    allocated: D = D("0"),
) -> SlotCapacity:
    return SlotCapacity(
        slot_id=uuid4(),
        line_id=line_id,
        slot_date=day,
        shift_code=shift,
        available_operator_minutes=available_minutes,
        planned_efficiency=efficiency,
        allocated_standard_minutes=allocated,
    )


def test_required_standard_minutes_negative_units_raises() -> None:
    with pytest.raises(ValueError, match="units"):
        required_standard_minutes(-1, D("10"))


def test_required_standard_minutes_zero_sam_raises() -> None:
    with pytest.raises(ValueError, match="sam"):
        required_standard_minutes(10, D("0"))


def test_available_standard_minutes_rejects_efficiency_out_of_range() -> None:
    with pytest.raises(ValueError):
        available_standard_minutes([D("100")], D("0"))
    with pytest.raises(ValueError):
        available_standard_minutes([D("100")], D("1.1"))


def test_utilization_none_on_zero_available() -> None:
    assert utilization(D("100"), D("0")) is None


def test_utilization_computes_fraction() -> None:
    assert utilization(D("50"), D("200")) == D("0.25")


def test_plan_fills_earliest_slots_first_and_respects_tie_break() -> None:
    day1 = date(2026, 1, 5)
    day2 = date(2026, 1, 6)
    slots = [
        _slot(line_id=LINE_B, day=day1, shift="B", available_minutes=D("100")),
        _slot(line_id=LINE_A, day=day1, shift="A", available_minutes=D("100")),
        _slot(line_id=LINE_A, day=day2, shift="A", available_minutes=D("100")),
    ]
    plan = plan_earliest_slots(
        units=15,
        sam_minutes_per_unit=D("10"),
        slots=slots,
        earliest_date=day1,
        due_date=day2,
        compatible_line_ids=frozenset({LINE_A, LINE_B}),
    )
    # 15 units * 10 SAM = 150 required minutes; day1/A (100 min) fills first
    # by the (date, shift, line_id) tie-break, then day1/B supplies the rest.
    assert plan.allocations[0].slot_date == day1
    assert plan.allocations[0].shift_code == "A"
    assert plan.allocations[0].units == D("10")
    assert plan.allocations[1].shift_code == "B"
    assert plan.allocated_units == D("15")
    assert plan.unscheduled_units == D("0")
    assert plan.unscheduled_reason is None
    assert plan.finish_date == day1


def test_plan_leaves_no_compatible_line() -> None:
    day1 = date(2026, 1, 5)
    slots = [_slot(line_id=LINE_A, day=day1)]
    plan = plan_earliest_slots(
        units=10,
        sam_minutes_per_unit=D("10"),
        slots=slots,
        earliest_date=day1,
        due_date=day1,
        compatible_line_ids=frozenset({LINE_B}),
    )
    assert plan.allocations == ()
    assert plan.allocated_units == D("0")
    assert plan.unscheduled_units == D("10")
    assert plan.unscheduled_reason == "NO_COMPATIBLE_LINE"
    assert plan.finish_date is None


def test_plan_honours_due_date_cutoff() -> None:
    day1 = date(2026, 1, 5)
    due = date(2026, 1, 5)
    after_due = date(2026, 1, 6)
    slots = [
        _slot(line_id=LINE_A, day=day1, available_minutes=D("50")),
        _slot(line_id=LINE_A, day=after_due, available_minutes=D("500")),
    ]
    plan = plan_earliest_slots(
        units=10,
        sam_minutes_per_unit=D("10"),
        slots=slots,
        earliest_date=day1,
        due_date=due,
        compatible_line_ids=frozenset({LINE_A}),
    )
    # Only day1 (<= due_date) is usable; it has 50 minutes = 5 units.
    assert len(plan.allocations) == 1
    assert plan.allocations[0].slot_date == day1
    assert plan.allocated_units == D("5")
    assert plan.unscheduled_units == D("5")
    assert plan.unscheduled_reason == "INSUFFICIENT_CAPACITY_BEFORE_DUE_DATE"


def test_plan_max_units_produces_limited_by_material() -> None:
    day1 = date(2026, 1, 5)
    day2 = date(2026, 1, 10)
    slots = [_slot(line_id=LINE_A, day=day1, available_minutes=D("1000"))]
    plan = plan_earliest_slots(
        units=50,
        sam_minutes_per_unit=D("10"),
        slots=slots,
        earliest_date=day1,
        due_date=day2,
        compatible_line_ids=frozenset({LINE_A}),
        max_units=D("20"),
    )
    assert plan.allocated_units == D("20")
    assert plan.unscheduled_units == D("30")
    assert plan.unscheduled_reason == "LIMITED_BY_MATERIAL"


def test_plan_allocation_never_exceeds_slot_remaining() -> None:
    day1 = date(2026, 1, 5)
    slots = [_slot(line_id=LINE_A, day=day1, available_minutes=D("33"))]
    plan = plan_earliest_slots(
        units=100,
        sam_minutes_per_unit=D("10"),
        slots=slots,
        earliest_date=day1,
        due_date=day1 + timedelta(days=1),
        compatible_line_ids=frozenset({LINE_A}),
    )
    assert plan.allocations[0].standard_minutes <= D("33")
