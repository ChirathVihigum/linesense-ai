"""The `PO-DEMO-001` walkthrough scenario (Task 6 brief section 4).

Pure data plus small pure helpers describing the one order every demo
walkthrough and screenshot anchors on. ``app.seed.generator`` is the only
caller that writes these values to the database; this module never touches
a session so its numbers can be asserted against directly in unit tests
without a database.

All figures below are deliberately chosen so that, combined with the
domain functions in ``app.domain.inventory.calc`` and
``app.domain.planning.calc``, they reproduce the exact reference numbers
recorded in the brief:

- ``available_now(1500, 400) == 1100``
- ``gross_demand(1000, 1.2, 0.05) == 1260``
- ``shortage(1100, 1260) == 160``
- ``coverable_units(1100, 1.2, 0.05) == 873``
"""

from __future__ import annotations

from decimal import Decimal

# --- Order identity -------------------------------------------------------

DEMO_CUSTOMER_CODE = "C07"
DEMO_STYLE_CODE = "ST-03"
DEMO_ORDER_QUANTITY = 1000
DEMO_ORDER_DUE_OFFSET_DAYS = 5
DEMO_ORDER_PRIORITY = 2

# --- Style operations (fixed — must stay compatible with every KTN line,
# including L6, which lacks the BH skill) ----------------------------------

# (sequence, code, name, sam_minutes, skill_code, machine_type)
DEMO_STYLE_OPERATIONS: tuple[tuple[int, str, str, Decimal, str, str], ...] = (
    (1, "OP-01", "shoulder join", Decimal("0.80"), "OL", "overlock"),
    (2, "OP-02", "collar attach", Decimal("1.20"), "SNLS", "single-needle"),
    (3, "OP-03", "placket attach", Decimal("1.00"), "SNLS", "single-needle"),
    (4, "OP-04", "sleeve set", Decimal("1.50"), "OL", "overlock"),
    (5, "OP-05", "side seam", Decimal("0.90"), "OL", "overlock"),
    (6, "OP-06", "bottom hem", Decimal("0.70"), "FL", "flatlock"),
    (7, "OP-07", "pressing", Decimal("0.60"), "PRESS", "press"),
)

DEMO_STYLE_SAM_TOTAL: Decimal = sum(
    (sam for _, _, _, sam, _, _ in DEMO_STYLE_OPERATIONS), Decimal("0")
)

# --- BOM -------------------------------------------------------------------

DEMO_BOM_MATERIAL_CODE = "M01"
DEMO_BOM_QUANTITY_PER_UNIT = Decimal("1.2")
DEMO_BOM_WASTAGE_FRACTION = Decimal("0.05")

# --- Material M01 ledger/balance -------------------------------------------

DEMO_BALANCE_ON_HAND_ACCEPTED = Decimal("1500")
DEMO_BALANCE_RESERVED = Decimal("400")
DEMO_OTHER_RESERVATION_QUANTITY = Decimal("400")

DEMO_ISSUE_WINDOW_DAYS = 14
DEMO_ISSUE_DAILY_QUANTITY = Decimal("90")
DEMO_RECEIPT_LOT_QUANTITY = (
    DEMO_BALANCE_ON_HAND_ACCEPTED + DEMO_ISSUE_DAILY_QUANTITY * DEMO_ISSUE_WINDOW_DAYS
)

DEMO_EXPECTED_RECEIPT_QUANTITY = Decimal("500")
DEMO_EXPECTED_RECEIPT_OFFSET_DAYS = 8  # anchor + 8, strictly after the order's due date

# --- Expected deterministic inventory results (asserted verbatim in tests) -

DEMO_EXPECTED_AVAILABLE = Decimal("1100")
DEMO_EXPECTED_GROSS_DEMAND = Decimal("1260")
DEMO_EXPECTED_SHORTAGE = Decimal("160")
DEMO_EXPECTED_COVERABLE_UNITS = Decimal("873")

# --- IE: OP-04 bottleneck on L2 (~60s effective cycle) ----------------------

DEMO_IE_LINE_CODE = "L2"
DEMO_IE_BOTTLENECK_OPERATION_CODE = "OP-04"
# Representative (median) observed seconds per operation, in sequence order,
# matching DEMO_STYLE_OPERATIONS. Two operators work OP-04 in parallel, so
# its *effective* cycle is 120s / 2 = 60s -- the highest of the seven,
# making it this line's bottleneck.
DEMO_IE_OBSERVED_SECONDS_BY_OPERATION: dict[str, tuple[Decimal, ...]] = {
    "OP-01": (Decimal("38"), Decimal("40"), Decimal("39"), Decimal("41"), Decimal("40")),
    "OP-02": (Decimal("45"), Decimal("47"), Decimal("46"), Decimal("48"), Decimal("46")),
    "OP-03": (Decimal("40"), Decimal("41"), Decimal("39"), Decimal("42"), Decimal("41")),
    "OP-04": (Decimal("118"), Decimal("120"), Decimal("121"), Decimal("119"), Decimal("122")),
    "OP-05": (Decimal("35"), Decimal("36"), Decimal("34"), Decimal("37"), Decimal("36")),
    "OP-06": (Decimal("28"), Decimal("29"), Decimal("27"), Decimal("30"), Decimal("29")),
    "OP-07": (Decimal("25"), Decimal("24"), Decimal("26"), Decimal("25"), Decimal("24")),
}
DEMO_IE_PARALLEL_OPERATORS_BY_OPERATION: dict[str, int] = {
    "OP-01": 1,
    "OP-02": 1,
    "OP-03": 1,
    "OP-04": 2,
    "OP-05": 1,
    "OP-06": 1,
    "OP-07": 1,
}
DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_LOW = Decimal("58")
DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_HIGH = Decimal("62")

__all__ = [
    "DEMO_BALANCE_ON_HAND_ACCEPTED",
    "DEMO_BALANCE_RESERVED",
    "DEMO_BOM_MATERIAL_CODE",
    "DEMO_BOM_QUANTITY_PER_UNIT",
    "DEMO_BOM_WASTAGE_FRACTION",
    "DEMO_CUSTOMER_CODE",
    "DEMO_EXPECTED_AVAILABLE",
    "DEMO_EXPECTED_COVERABLE_UNITS",
    "DEMO_EXPECTED_GROSS_DEMAND",
    "DEMO_EXPECTED_RECEIPT_OFFSET_DAYS",
    "DEMO_EXPECTED_RECEIPT_QUANTITY",
    "DEMO_EXPECTED_SHORTAGE",
    "DEMO_IE_BOTTLENECK_OPERATION_CODE",
    "DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_HIGH",
    "DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_LOW",
    "DEMO_IE_LINE_CODE",
    "DEMO_IE_OBSERVED_SECONDS_BY_OPERATION",
    "DEMO_IE_PARALLEL_OPERATORS_BY_OPERATION",
    "DEMO_ISSUE_DAILY_QUANTITY",
    "DEMO_ISSUE_WINDOW_DAYS",
    "DEMO_ORDER_DUE_OFFSET_DAYS",
    "DEMO_ORDER_PRIORITY",
    "DEMO_ORDER_QUANTITY",
    "DEMO_OTHER_RESERVATION_QUANTITY",
    "DEMO_RECEIPT_LOT_QUANTITY",
    "DEMO_STYLE_CODE",
    "DEMO_STYLE_OPERATIONS",
    "DEMO_STYLE_SAM_TOTAL",
]
