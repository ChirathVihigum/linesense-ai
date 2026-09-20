"""Unit tests for `app.evaluation.agent_eval._respects_material_coverage`
(polish batch, Task 19: the Decimal conversion around a malformed eval
fixture used to be caught by a bare `except Exception`, silently discarding
the failure reason)."""

from __future__ import annotations

from decimal import Decimal

from app.evaluation.agent_eval import _respects_material_coverage


def test_none_allocated_is_none_with_no_error() -> None:
    result, error = _respects_material_coverage(None, Decimal(100))
    assert result is None
    assert error is None


def test_within_coverage_is_true() -> None:
    result, error = _respects_material_coverage("50", Decimal(100))
    assert result is True
    assert error is None


def test_over_coverage_is_false() -> None:
    result, error = _respects_material_coverage("150", Decimal(100))
    assert result is False
    assert error is None


def test_malformed_value_is_none_with_a_recorded_error() -> None:
    result, error = _respects_material_coverage("not-a-number", Decimal(100))
    assert result is None
    assert error is not None
    assert "InvalidOperation" in error


def test_non_scalar_value_is_none_with_a_recorded_error() -> None:
    result, error = _respects_material_coverage({"unexpected": "shape"}, Decimal(100))
    assert result is None
    assert error is not None
