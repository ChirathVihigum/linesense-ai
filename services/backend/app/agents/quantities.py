"""Decimal formatting shared by the agents and the recommendation builder.

Action payloads are JSON: every quantity crosses the protocol boundary as a
plain decimal **string** (never a float, never scientific notation), so that
the number a reviewer approves is byte-for-byte the number the agent
computed.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# Greedy allocation divides by SAM minutes, so raw units carry the full
# 28-digit Decimal context. Six places is the same tolerance
# ``app.domain.planning.calc`` uses to compare plans.
QUANTITY_PLACES = Decimal("0.000001")


def quantize_quantity(value: Decimal) -> Decimal:
    """``value`` rounded to six decimal places (half up), trailing zeros removed."""
    return value.quantize(QUANTITY_PLACES, rounding=ROUND_HALF_UP).normalize()


def decimal_str(value: Decimal) -> str:
    """``value`` as a plain decimal string (``"873"``, ``"1099.98"``)."""
    return format(quantize_quantity(value), "f")
