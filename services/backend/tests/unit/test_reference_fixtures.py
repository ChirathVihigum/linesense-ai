"""The plan's independently-worked reference fixtures (see
`docs/architecture/formulas.md`). These four are pure arithmetic checks;
fixtures 5 and 6 (concurrency / staleness) are database-backed and live in
Task 13's integration tests.
"""

from __future__ import annotations

from decimal import Decimal as D

from app.domain.ie.calc import line_balance
from app.domain.inventory.calc import available_now, gross_demand, shortage
from app.domain.planning.calc import available_standard_minutes, required_standard_minutes
from app.domain.quality.calc import defective_rate, defects_per_hundred_units


def test_fixture_planning_one_shift_capacity() -> None:
    demand = required_standard_minutes(1000, D("12"))
    capacity = available_standard_minutes([D("420")] * 20, D("0.75"))
    assert demand == D("12000")
    assert capacity == D("6300.00")
    assert demand > capacity  # cannot fit in one shift


def test_fixture_material_shortage() -> None:
    avail = available_now(D("1500"), D("400"))
    demand = gross_demand(D("1000"), D("1.2"), D("0.05"))
    assert avail == D("1100")
    assert demand == D("1260.000")
    assert shortage(avail, demand) == D("160.000")


def test_fixture_line_balance() -> None:
    r = line_balance([D("40"), D("60"), D("50")])
    assert r.bottleneck_effective_seconds == D("60")
    assert r.units_per_hour == D("60")
    assert r.balance_index_percent.quantize(D("0.01")) == D("83.33")


def test_fixture_quality_rates() -> None:
    assert defective_rate(7, 100) == D("0.07")
    assert defects_per_hundred_units(12, 100) == D("12")
