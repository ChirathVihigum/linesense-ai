"""Unit tests for `app.domain.quality.calc` beyond the reference fixtures."""

from __future__ import annotations

import pytest

from app.domain.quality.calc import (
    InspectionEvaluation,
    QualityPolicyRules,
    ShipmentFacts,
    defective_rate,
    defects_per_hundred_units,
    evaluate_inspection,
    quality_state,
    shipment_eligibility,
)
from app.domain.vocab import ProductionState, QualityState

RULES = QualityPolicyRules(
    sample_size=20,
    max_defective_units=2,
    max_critical_defects=0,
    required_inspection_types=("FINAL",),
)


def test_defective_rate_none_when_inspected_zero() -> None:
    assert defective_rate(0, 0) is None


def test_defects_per_hundred_units_none_when_inspected_zero() -> None:
    assert defects_per_hundred_units(5, 0) is None


def test_quality_policy_rules_from_json() -> None:
    rules = QualityPolicyRules.from_json(
        {
            "sample_size": 20,
            "max_defective_units": 2,
            "max_critical_defects": 0,
            "required_inspection_types": ["FINAL"],
        }
    )
    assert rules == RULES


@pytest.mark.parametrize(
    "field,value",
    [
        ("sample_size", -1),
        ("max_defective_units", -1),
        ("max_critical_defects", -1),
    ],
)
def test_quality_policy_rules_from_json_rejects_negative_ints(field: str, value: int) -> None:
    data = {
        "sample_size": 20,
        "max_defective_units": 2,
        "max_critical_defects": 0,
        "required_inspection_types": ["FINAL"],
        field: value,
    }
    with pytest.raises(ValueError):
        QualityPolicyRules.from_json(data)


def test_quality_policy_rules_from_json_rejects_empty_required_types() -> None:
    with pytest.raises(ValueError):
        QualityPolicyRules.from_json(
            {
                "sample_size": 20,
                "max_defective_units": 2,
                "max_critical_defects": 0,
                "required_inspection_types": [],
            }
        )


def test_evaluate_inspection_insufficient_sample() -> None:
    result = evaluate_inspection(RULES, inspected_units=5, defective_units=0, critical_defects=0)
    assert result == InspectionEvaluation(result="INSUFFICIENT_SAMPLE", reasons=())


def test_evaluate_inspection_insufficient_sample_when_zero_inspected() -> None:
    result = evaluate_inspection(RULES, inspected_units=0, defective_units=0, critical_defects=0)
    assert result.result == "INSUFFICIENT_SAMPLE"


def test_evaluate_inspection_pass() -> None:
    result = evaluate_inspection(RULES, inspected_units=20, defective_units=1, critical_defects=0)
    assert result == InspectionEvaluation(result="PASS", reasons=())


def test_evaluate_inspection_fail_lists_each_broken_rule() -> None:
    result = evaluate_inspection(RULES, inspected_units=20, defective_units=5, critical_defects=1)
    assert result.result == "FAIL"
    assert "DEFECTIVE_UNITS_EXCEEDED" in result.reasons
    assert "CRITICAL_DEFECTS_EXCEEDED" in result.reasons
    assert len(result.reasons) == 2


def _facts(**overrides: object) -> ShipmentFacts:
    base: dict[str, object] = {
        "production_state": ProductionState.PRODUCTION_COMPLETE.value,
        "quantity": 100,
        "packed_units": 100,
        "passed_inspection_types": frozenset({"FINAL"}),
        "required_inspection_types": frozenset({"FINAL"}),
        "has_active_hold": False,
        "has_valid_release": True,
        "policy_known": True,
    }
    base.update(overrides)
    return ShipmentFacts(**base)  # type: ignore[arg-type]


def test_shipment_eligibility_true_when_all_conditions_met() -> None:
    result = shipment_eligibility(_facts())
    assert result.eligible is True
    assert result.reasons == ()


@pytest.mark.parametrize(
    "overrides,expected_reason",
    [
        ({"policy_known": False}, "POLICY_UNKNOWN"),
        ({"production_state": ProductionState.IN_PRODUCTION.value}, "PRODUCTION_NOT_COMPLETE"),
        ({"packed_units": 50}, "PACKING_INCOMPLETE"),
        ({"passed_inspection_types": frozenset()}, "INSPECTION_MISSING:FINAL"),
        ({"has_active_hold": True}, "ACTIVE_QUALITY_HOLD"),
        ({"has_valid_release": False}, "NO_QUALITY_RELEASE"),
    ],
)
def test_shipment_eligibility_false_for_each_missing_condition(
    overrides: dict[str, object], expected_reason: str
) -> None:
    result = shipment_eligibility(_facts(**overrides))
    assert result.eligible is False
    assert expected_reason in result.reasons


@pytest.mark.parametrize(
    "has_inspection,has_active_hold,has_valid_release,expected",
    [
        (False, False, False, QualityState.NOT_INSPECTED),
        (False, True, True, QualityState.NOT_INSPECTED),
        (True, True, False, QualityState.HOLD),
        (True, True, True, QualityState.HOLD),
        (True, False, True, QualityState.RELEASED),
        (True, False, False, QualityState.PENDING),
    ],
)
def test_quality_state_matrix(
    has_inspection: bool,
    has_active_hold: bool,
    has_valid_release: bool,
    expected: QualityState,
) -> None:
    assert (
        quality_state(
            has_inspection=has_inspection,
            has_active_hold=has_active_hold,
            has_valid_release=has_valid_release,
        )
        == expected
    )
