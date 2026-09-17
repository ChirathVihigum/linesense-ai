"""Inventory: availability, demand, projected balance, reorder point, and
material readiness.

Pure functions; no database access or I/O. See
`docs/architecture/formulas.md` for the underlying formulas and the
plan's reference fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from app.domain.vocab import MaterialState

APPROVED_CONVERSIONS: Mapping[tuple[str, str], Decimal] = {
    ("m", "m"): Decimal("1"),
    ("kg", "kg"): Decimal("1"),
    ("pcs", "pcs"): Decimal("1"),
    ("cone", "cone"): Decimal("1"),
    ("cm", "m"): Decimal("0.01"),
    ("g", "kg"): Decimal("0.001"),
}


class UnsupportedUnitConversion(ValueError):
    """Raised when no approved conversion factor exists for a unit pair."""


def available_now(accepted_on_hand: Decimal, active_reservations: Decimal) -> Decimal:
    """`accepted_on_hand - active_reservations`.

    Active reservations are already excluded here, so callers must never
    subtract them again from downstream demand.
    """
    return accepted_on_hand - active_reservations


def gross_demand(
    planned_units: Decimal, quantity_per_unit: Decimal, wastage_fraction: Decimal
) -> Decimal:
    """`planned_units * quantity_per_unit * (1 + wastage_fraction)`."""
    return planned_units * quantity_per_unit * (Decimal(1) + wastage_fraction)


def shortage(available: Decimal, demand: Decimal) -> Decimal:
    """`max(0, demand - available)` — a shortage is never reported negative."""
    deficit = demand - available
    return deficit if deficit > 0 else Decimal(0)


def projected_balance(
    *,
    available_now: Decimal,
    receipts: Sequence[tuple[date, Decimal]],
    demand: Sequence[tuple[date, Decimal]],
    at: date,
) -> Decimal:
    """`available_now + eligible_receipts_by(at) - new_demand_by(at)`.

    Only receipts and demand dated on or before `at` count.
    """
    eligible_receipts = sum((qty for when, qty in receipts if when <= at), Decimal(0))
    eligible_demand = sum((qty for when, qty in demand if when <= at), Decimal(0))
    return available_now + eligible_receipts - eligible_demand


def reorder_point(
    expected_daily_consumption: Decimal, lead_time_days: int, safety_stock: Decimal
) -> Decimal:
    """`expected_daily_consumption * lead_time_days + safety_stock`."""
    return expected_daily_consumption * lead_time_days + safety_stock


def coverage_days(available: Decimal, expected_daily_consumption: Decimal) -> Decimal | None:
    """`available / expected_daily_consumption`, or `None` (unknown/unbounded)
    when consumption is zero or negative — never divide by zero or report a
    misleading computed value.
    """
    if expected_daily_consumption <= 0:
        return None
    return available / expected_daily_consumption


def average_daily_consumption(
    issues: Sequence[tuple[date, Decimal]], window_days: int, as_of: date
) -> Decimal:
    """Average of absolute issue quantities dated in
    `(as_of - window_days, as_of]`, divided by `window_days`.
    """
    lower_bound_exclusive = as_of - timedelta(days=window_days)
    total = sum(
        (abs(qty) for when, qty in issues if lower_bound_exclusive < when <= as_of),
        Decimal(0),
    )
    return total / window_days


def convert_quantity(
    quantity: Decimal,
    from_unit: str,
    to_unit: str,
    rules: Mapping[tuple[str, str], Decimal] = APPROVED_CONVERSIONS,
) -> Decimal:
    """Convert `quantity` from `from_unit` to `to_unit` using an approved,
    explicit conversion factor. Raises `UnsupportedUnitConversion` for any
    pair that is not on the approved list — never an implicit coercion.
    """
    factor = rules.get((from_unit, to_unit))
    if factor is None:
        raise UnsupportedUnitConversion(f"no approved conversion from {from_unit!r} to {to_unit!r}")
    return quantity * factor


def coverable_units(
    available: Decimal, quantity_per_unit: Decimal, wastage_fraction: Decimal
) -> Decimal:
    """`floor(available / (quantity_per_unit * (1 + wastage_fraction)))`, as
    a whole-number Decimal; `0` when `available` is not positive.
    """
    if available <= 0:
        return Decimal(0)
    per_unit_with_wastage = quantity_per_unit * (Decimal(1) + wastage_fraction)
    return (available / per_unit_with_wastage).to_integral_value(rounding=ROUND_FLOOR)


def material_state(
    shortage_qty: Decimal, projected_shortage_at_due: Decimal, data_complete: bool
) -> MaterialState:
    """Material readiness for an order.

    `UNKNOWN` when the underlying data isn't complete; `SHORTAGE` when
    there is a current shortage that is still projected to exist at the
    due date; `AT_RISK` when there is a current shortage but expected
    receipts cover it before the due date; otherwise `READY`.
    """
    if not data_complete:
        return MaterialState.UNKNOWN
    if shortage_qty > 0:
        if projected_shortage_at_due > 0:
            return MaterialState.SHORTAGE
        return MaterialState.AT_RISK
    return MaterialState.READY
