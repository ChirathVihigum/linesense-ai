"""Calculation section: re-run the plan's independently-worked reference
fixtures (see ``tests/unit/test_reference_fixtures.py`` and
``docs/architecture/formulas.md``) and record pass/fail honestly rather
than asserting (an evaluation run must never crash on a missed target;
see task-19-brief.md requirement 5 and requirement 9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any

from app.domain.ie.calc import line_balance
from app.domain.inventory.calc import available_now, gross_demand, shortage
from app.domain.planning.calc import available_standard_minutes, required_standard_minutes
from app.domain.quality.calc import defective_rate, defects_per_hundred_units


@dataclass(frozen=True)
class CalcCheck:
    name: str
    passed: bool
    error: str | None
    values: dict[str, Any]


def _run(name: str, fn: Callable[[], dict[str, Any]]) -> CalcCheck:
    try:
        values = fn()
    except AssertionError as exc:
        return CalcCheck(name=name, passed=False, error=str(exc) or repr(exc), values={})
    return CalcCheck(name=name, passed=True, error=None, values=values)


def _check_planning_one_shift_capacity() -> dict[str, Any]:
    demand = required_standard_minutes(1000, D("12"))
    capacity = available_standard_minutes([D("420")] * 20, D("0.75"))
    assert demand == D("12000")
    assert capacity == D("6300.00")
    assert demand > capacity  # cannot fit in one shift
    return {"demand": str(demand), "capacity": str(capacity)}


def _check_material_shortage() -> dict[str, Any]:
    avail = available_now(D("1500"), D("400"))
    demand = gross_demand(D("1000"), D("1.2"), D("0.05"))
    assert avail == D("1100")
    assert demand == D("1260.000")
    computed_shortage = shortage(avail, demand)
    assert computed_shortage == D("160.000")
    return {"available": str(avail), "demand": str(demand), "shortage": str(computed_shortage)}


def _check_line_balance() -> dict[str, Any]:
    result = line_balance([D("40"), D("60"), D("50")])
    assert result.bottleneck_effective_seconds == D("60")
    assert result.units_per_hour == D("60")
    balance_index = result.balance_index_percent.quantize(D("0.01"))
    assert balance_index == D("83.33")
    return {
        "bottleneck_effective_seconds": str(result.bottleneck_effective_seconds),
        "units_per_hour": str(result.units_per_hour),
        "balance_index_percent": str(balance_index),
    }


def _check_quality_rates() -> dict[str, Any]:
    rate = defective_rate(7, 100)
    dhu = defects_per_hundred_units(12, 100)
    assert rate == D("0.07")
    assert dhu == D("12")
    return {"defective_rate": str(rate), "defects_per_hundred_units": str(dhu)}


CHECKS: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("planning_one_shift_capacity", _check_planning_one_shift_capacity),
    ("material_shortage", _check_material_shortage),
    ("line_balance", _check_line_balance),
    ("quality_rates", _check_quality_rates),
)


def run_calc_eval() -> dict[str, Any]:
    """Execute every reference fixture and report pass/fail per check.

    Never raises on a failed check: a regression in a domain formula is a
    finding this report exists to surface, not a reason to crash the
    harness (task-19-brief.md requirement 9 — targets are reported
    honestly, including misses).
    """
    checks = [_run(name, fn) for name, fn in CHECKS]
    return {
        "checks": [
            {"name": c.name, "passed": c.passed, "error": c.error, "values": c.values}
            for c in checks
        ],
        "total": len(checks),
        "passed": sum(1 for c in checks if c.passed),
        "all_passed": all(c.passed for c in checks),
    }
