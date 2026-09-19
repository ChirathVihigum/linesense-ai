"""Unit tests for `app.domain.ie.calc` beyond the reference fixtures."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.domain.ie.calc import (
    InsufficientSamples,
    effective_cycle_seconds,
    line_balance,
    observed_units_per_hour,
    representative_cycle_seconds,
    sam_capacity_units_per_hour,
)


def test_representative_cycle_seconds_is_median() -> None:
    assert representative_cycle_seconds([D("40"), D("42"), D("100"), D("41")]) == D("41.5")


def test_representative_cycle_seconds_raises_below_min_samples() -> None:
    with pytest.raises(InsufficientSamples):
        representative_cycle_seconds([D("40"), D("42")])


def test_effective_cycle_seconds_halves_with_two_parallel_operators() -> None:
    assert effective_cycle_seconds(D("60"), 2) == D("30")


def test_line_balance_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        line_balance([])


def test_line_balance_bottleneck_is_first_on_ties() -> None:
    result = line_balance([D("50"), D("50"), D("30")])
    assert result.bottleneck_index == 0
    assert result.bottleneck_effective_seconds == D("50")


def test_sam_capacity_units_per_hour() -> None:
    # 5 operators, SAM 12 min/unit, 75% efficiency -> 5*60*0.75/12 = 18.75
    assert sam_capacity_units_per_hour(5, D("12"), D("0.75")) == D("18.75")


def test_observed_units_per_hour() -> None:
    assert observed_units_per_hour(120, D("4")) == D("30")


@pytest.mark.parametrize("sam_minutes_per_unit", [D("0"), D("-1")])
def test_sam_capacity_units_per_hour_rejects_non_positive_sam(sam_minutes_per_unit: D) -> None:
    with pytest.raises(ValueError, match="sam_minutes_per_unit"):
        sam_capacity_units_per_hour(5, sam_minutes_per_unit, D("0.75"))


@pytest.mark.parametrize("hours", [D("0"), D("-2")])
def test_observed_units_per_hour_rejects_non_positive_hours(hours: D) -> None:
    with pytest.raises(ValueError, match="hours"):
        observed_units_per_hour(120, hours)
