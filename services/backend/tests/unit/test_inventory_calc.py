"""Unit tests for `app.domain.inventory.calc` beyond the reference fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.domain.inventory.calc import (
    APPROVED_CONVERSIONS,
    UnsupportedUnitConversion,
    average_daily_consumption,
    convert_quantity,
    coverable_units,
    coverage_days,
    material_state,
    projected_balance,
    reorder_point,
    shortage,
)
from app.domain.vocab import MaterialState


def test_coverage_days_none_on_zero_consumption() -> None:
    assert coverage_days(D("500"), D("0")) is None
    assert coverage_days(D("500"), D("-1")) is None


def test_coverage_days_computes_ratio() -> None:
    assert coverage_days(D("500"), D("100")) == D("5")


def test_projected_balance_counts_receipts_only_up_to_at() -> None:
    at = date(2026, 2, 10)
    receipts = [(date(2026, 2, 5), D("100")), (date(2026, 2, 15), D("999"))]
    demand = [(date(2026, 2, 9), D("40")), (date(2026, 2, 11), D("999"))]
    balance = projected_balance(available_now=D("50"), receipts=receipts, demand=demand, at=at)
    # 50 + 100 (received by 2/10) - 40 (demanded by 2/10) = 110; the 2/15
    # receipt and 2/11 demand fall after `at` and must not be counted.
    assert balance == D("110")


def test_reorder_point() -> None:
    assert reorder_point(D("20"), 7, D("50")) == D("190")


def test_unsupported_conversion_raises() -> None:
    with pytest.raises(UnsupportedUnitConversion):
        convert_quantity(D("10"), "kg", "m")


def test_convert_quantity_uses_approved_factor() -> None:
    assert convert_quantity(D("250"), "cm", "m", APPROVED_CONVERSIONS) == D("2.50")


def test_coverable_units_floors_to_whole_units() -> None:
    assert coverable_units(D("1100"), D("1.2"), D("0.05")) == D("873")


def test_coverable_units_zero_when_available_non_positive() -> None:
    assert coverable_units(D("0"), D("1.2"), D("0.05")) == D("0")
    assert coverable_units(D("-5"), D("1.2"), D("0.05")) == D("0")


def test_average_daily_consumption_windows_correctly() -> None:
    as_of = date(2026, 2, 10)
    issues = [
        (date(2026, 1, 20), D("1000")),  # outside the 7-day window, excluded
        (date(2026, 2, 4), D("30")),  # exactly on the exclusive lower bound, excluded
        (date(2026, 2, 5), D("10")),
        (date(2026, 2, 10), D("4")),  # on `as_of`, included
    ]
    assert average_daily_consumption(issues, window_days=6, as_of=as_of) == D("14") / D("6")


def test_material_state_unknown_when_data_incomplete() -> None:
    assert material_state(D("10"), D("10"), data_complete=False) == MaterialState.UNKNOWN


def test_material_state_shortage() -> None:
    assert material_state(D("10"), D("5"), data_complete=True) == MaterialState.SHORTAGE


def test_material_state_at_risk_when_covered_before_due() -> None:
    assert material_state(D("10"), D("0"), data_complete=True) == MaterialState.AT_RISK


def test_material_state_ready_when_no_shortage() -> None:
    assert material_state(D("0"), D("0"), data_complete=True) == MaterialState.READY


def test_shortage_never_negative() -> None:
    assert shortage(D("500"), D("100")) == D("0")
