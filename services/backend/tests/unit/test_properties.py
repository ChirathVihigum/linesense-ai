"""Property-based tests for the domain calculation layer.

Strategies are kept deliberately bounded (fixed-precision Decimals, small
collections) so `max_examples=200` runs fast and `derandomize=True` keeps
runs reproducible in CI.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.ie.calc import line_balance
from app.domain.inventory.calc import available_now, shortage
from app.domain.planning.calc import SlotCapacity, plan_earliest_slots

_LINE_POOL: tuple[UUID, UUID] = (uuid4(), uuid4())

_MINUTES = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("500"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_EFFICIENCY = st.decimals(
    min_value=Decimal("0.10"),
    max_value=Decimal("1.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_SAM = st.decimals(
    min_value=Decimal("0.50"),
    max_value=Decimal("20.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_ALLOCATED_FRACTION = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("1.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_QUANTITY = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("100000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_CYCLE_SECONDS = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("600.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

PlanArgs = tuple[int, Decimal, list[SlotCapacity], date, date, frozenset[UUID]]


@st.composite
def _plan_args(draw: st.DrawFn) -> PlanArgs:
    units = draw(st.integers(min_value=0, max_value=50))
    sam = draw(_SAM)
    slot_count = draw(st.integers(min_value=1, max_value=5))
    earliest = date(2026, 1, 1)
    due = earliest + timedelta(days=draw(st.integers(min_value=0, max_value=10)))

    slots: list[SlotCapacity] = []
    for _ in range(slot_count):
        line_id = draw(st.sampled_from(_LINE_POOL))
        day_offset = draw(st.integers(min_value=-2, max_value=12))
        shift = draw(st.sampled_from(["A", "B"]))
        available = draw(_MINUTES)
        efficiency = draw(_EFFICIENCY)
        allocated_fraction = draw(_ALLOCATED_FRACTION)
        allocated = (available * efficiency * allocated_fraction).quantize(Decimal("0.01"))
        slots.append(
            SlotCapacity(
                slot_id=uuid4(),
                line_id=line_id,
                slot_date=earliest + timedelta(days=day_offset),
                shift_code=shift,
                available_operator_minutes=available,
                planned_efficiency=efficiency,
                allocated_standard_minutes=allocated,
            )
        )

    compatible = draw(
        st.sampled_from([frozenset(), frozenset({_LINE_POOL[0]}), frozenset(_LINE_POOL)])
    )
    return units, sam, slots, earliest, due, compatible


@settings(max_examples=200, deadline=None, derandomize=True)
@given(_plan_args())
def test_plan_allocations_never_exceed_slot_remaining_and_totals_reconcile(
    args: PlanArgs,
) -> None:
    units, sam, slots, earliest, due, compatible = args

    plan = plan_earliest_slots(
        units=units,
        sam_minutes_per_unit=sam,
        slots=slots,
        earliest_date=earliest,
        due_date=due,
        compatible_line_ids=compatible,
    )

    by_id = {slot.slot_id: slot for slot in slots}
    for allocation in plan.allocations:
        assert allocation.standard_minutes <= by_id[allocation.slot_id].remaining_standard_minutes

    total = (plan.allocated_units + plan.unscheduled_units).quantize(Decimal("0.000001"))
    assert total == Decimal(units).quantize(Decimal("0.000001"))


@settings(max_examples=200, deadline=None, derandomize=True)
@given(available=_QUANTITY, demand=_QUANTITY)
def test_shortage_never_negative(available: Decimal, demand: Decimal) -> None:
    assert shortage(available, demand) >= Decimal("0")


@settings(max_examples=200, deadline=None, derandomize=True)
@given(on_hand=_QUANTITY, reservations=_QUANTITY, demand=_QUANTITY)
def test_available_now_with_larger_reservations_never_makes_shortage_negative(
    on_hand: Decimal, reservations: Decimal, demand: Decimal
) -> None:
    available = available_now(on_hand, reservations)
    assert shortage(available, demand) >= Decimal("0")


@settings(max_examples=200, deadline=None, derandomize=True)
@given(cycles=st.lists(_CYCLE_SECONDS, min_size=1, max_size=10))
def test_line_balance_index_within_bounds(cycles: list[Decimal]) -> None:
    result = line_balance(cycles)
    assert Decimal("0") < result.balance_index_percent <= Decimal("100")
