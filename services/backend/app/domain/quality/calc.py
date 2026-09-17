"""Quality: defect rates, inspection disposition against a versioned
policy, shipment eligibility, and quality state.

Pure functions and immutable dataclasses; no database access or I/O. See
`docs/architecture/formulas.md` for the underlying formulas and the
plan's reference fixture. Disposition and eligibility are decided only
against an explicit, versioned `QualityPolicyRules`/`ShipmentFacts` value
— never an LLM-invented threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app.domain.vocab import ProductionState, QualityState

InspectionResultLiteral = Literal["PASS", "FAIL", "INSUFFICIENT_SAMPLE"]


@dataclass(frozen=True)
class QualityPolicyRules:
    sample_size: int
    max_defective_units: int
    max_critical_defects: int
    required_inspection_types: tuple[str, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> QualityPolicyRules:
        sample_size = int(data["sample_size"])
        max_defective_units = int(data["max_defective_units"])
        max_critical_defects = int(data["max_critical_defects"])
        required_inspection_types = tuple(data["required_inspection_types"])

        for field_name, value in (
            ("sample_size", sample_size),
            ("max_defective_units", max_defective_units),
            ("max_critical_defects", max_critical_defects),
        ):
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0")
        if not required_inspection_types:
            raise ValueError("required_inspection_types must not be empty")

        return cls(
            sample_size=sample_size,
            max_defective_units=max_defective_units,
            max_critical_defects=max_critical_defects,
            required_inspection_types=required_inspection_types,
        )


@dataclass(frozen=True)
class InspectionEvaluation:
    result: InspectionResultLiteral
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ShipmentFacts:
    production_state: str
    quantity: int
    packed_units: int
    passed_inspection_types: frozenset[str]
    required_inspection_types: frozenset[str]
    has_active_hold: bool
    has_valid_release: bool
    policy_known: bool


@dataclass(frozen=True)
class ShipmentEligibility:
    eligible: bool
    reasons: tuple[str, ...]


def defective_rate(defective_units: int, inspected_units: int) -> Decimal | None:
    """`defective_units / inspected_units` as a fraction (0.07, not 7);
    `None` (unknown) when nothing was inspected.
    """
    if inspected_units == 0:
        return None
    return Decimal(defective_units) / Decimal(inspected_units)


def defects_per_hundred_units(total_defects: int, inspected_units: int) -> Decimal | None:
    """`total_defects / inspected_units * 100` (DHU); `None` when nothing
    was inspected.
    """
    if inspected_units == 0:
        return None
    return Decimal(total_defects) / Decimal(inspected_units) * 100


def evaluate_inspection(
    rules: QualityPolicyRules,
    *,
    inspected_units: int,
    defective_units: int,
    critical_defects: int,
) -> InspectionEvaluation:
    """Disposition against a versioned, approved policy: `INSUFFICIENT_SAMPLE`
    below the policy's `sample_size`; `FAIL` when either threshold is
    exceeded (one reason per broken rule); otherwise `PASS`.
    """
    if inspected_units < rules.sample_size:
        return InspectionEvaluation(result="INSUFFICIENT_SAMPLE", reasons=())

    reasons: list[str] = []
    if defective_units > rules.max_defective_units:
        reasons.append("DEFECTIVE_UNITS_EXCEEDED")
    if critical_defects > rules.max_critical_defects:
        reasons.append("CRITICAL_DEFECTS_EXCEEDED")

    if reasons:
        return InspectionEvaluation(result="FAIL", reasons=tuple(reasons))
    return InspectionEvaluation(result="PASS", reasons=())


def shipment_eligibility(facts: ShipmentFacts) -> ShipmentEligibility:
    """A shipment is eligible only when every gate holds: the applicable
    policy is known, production is complete, packing is complete, every
    required inspection type has passed, there is no active quality hold,
    and a valid quality release exists. `shipment_ready` is always
    computed here, never stored as truth and never chosen by a model.
    """
    reasons: list[str] = []

    if not facts.policy_known:
        reasons.append("POLICY_UNKNOWN")
    if facts.production_state != ProductionState.PRODUCTION_COMPLETE:
        reasons.append("PRODUCTION_NOT_COMPLETE")
    if facts.packed_units < facts.quantity:
        reasons.append("PACKING_INCOMPLETE")
    for missing_type in sorted(facts.required_inspection_types - facts.passed_inspection_types):
        reasons.append(f"INSPECTION_MISSING:{missing_type}")
    if facts.has_active_hold:
        reasons.append("ACTIVE_QUALITY_HOLD")
    if not facts.has_valid_release:
        reasons.append("NO_QUALITY_RELEASE")

    return ShipmentEligibility(eligible=not reasons, reasons=tuple(reasons))


def quality_state(
    *, has_inspection: bool, has_active_hold: bool, has_valid_release: bool
) -> QualityState:
    """`NOT_INSPECTED` before any inspection exists; `HOLD` while an active
    quality hold exists; `RELEASED` once a valid release exists; otherwise
    `PENDING`.
    """
    if not has_inspection:
        return QualityState.NOT_INSPECTED
    if has_active_hold:
        return QualityState.HOLD
    if has_valid_release:
        return QualityState.RELEASED
    return QualityState.PENDING
