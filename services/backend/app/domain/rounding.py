"""Decimal rounding helpers shared across the domain layer.

Per `docs/architecture/formulas.md` ("Rounding rules"): full `Decimal`
precision is retained through every intermediate calculation; rounding
happens only for *display* or for a quantity that must be purchased or
allocated in whole discrete units, and it always rounds in the safe
(never-under-allocate) direction.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal


def quantize_display(value: Decimal, places: int = 2) -> Decimal:
    """Round `value` to `places` decimal digits for display, half rounding up.

    This is a display-only transform; never feed the result back into
    further arithmetic.
    """
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def round_up_to_pack(quantity: Decimal, pack_size: Decimal | None) -> Decimal:
    """Ceil `quantity` up to the nearest whole multiple of `pack_size`.

    `pack_size is None` means the material has no defined pack size (many
    materials are continuous quantities, e.g. meters or kilograms); the
    quantity is returned unchanged in that case rather than rounded to an
    arbitrary whole unit. Under-ordering/under-allocating is always the
    unsafe direction, so a defined pack size always rounds up.
    """
    if pack_size is None:
        return quantity
    if pack_size <= 0:
        raise ValueError("pack_size must be positive")
    multiples = (quantity / pack_size).to_integral_value(rounding=ROUND_CEILING)
    return multiples * pack_size
