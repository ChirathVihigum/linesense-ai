"""Unit tests for `app.domain.orders.lifecycle` and `app.domain.rounding`."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.domain.orders.lifecycle import TRANSITIONS, InvalidTransition, get_transition
from app.domain.rounding import quantize_display, round_up_to_pack
from app.domain.vocab import ProductionState

EXPECTED_TRANSITIONS: dict[tuple[ProductionState, ProductionState], tuple[str, tuple[str, ...]]] = {
    (ProductionState.DRAFT, ProductionState.VALIDATED): (
        "order:transition",
        ("order_data_complete",),
    ),
    (ProductionState.VALIDATED, ProductionState.PLANNED): (
        "recommendation:apply",
        ("has_active_allocation",),
    ),
    (ProductionState.PLANNED, ProductionState.IN_PRODUCTION): ("order:transition", ()),
    (ProductionState.IN_PRODUCTION, ProductionState.PRODUCTION_COMPLETE): (
        "order:transition",
        ("produced_units_complete",),
    ),
    (ProductionState.PRODUCTION_COMPLETE, ProductionState.DISPATCHED): (
        "order:dispatch",
        ("shipment_eligible",),
    ),
    (ProductionState.DRAFT, ProductionState.CANCELLED): ("order:cancel", ()),
    (ProductionState.VALIDATED, ProductionState.CANCELLED): ("order:cancel", ()),
    (ProductionState.PLANNED, ProductionState.CANCELLED): ("order:cancel", ()),
}


def test_transitions_table_contains_exactly_the_expected_pairs() -> None:
    assert set(TRANSITIONS.keys()) == set(EXPECTED_TRANSITIONS.keys())


@pytest.mark.parametrize("pair", list(EXPECTED_TRANSITIONS.keys()))
def test_every_transition_matches_expected_permission_and_preconditions(
    pair: tuple[ProductionState, ProductionState],
) -> None:
    source, target = pair
    rule = get_transition(source, target)
    expected_permission, expected_preconditions = EXPECTED_TRANSITIONS[pair]
    assert rule.source == source
    assert rule.target == target
    assert rule.permission == expected_permission
    assert rule.preconditions == expected_preconditions


def test_invalid_transition_raises() -> None:
    with pytest.raises(InvalidTransition):
        get_transition(ProductionState.DISPATCHED, ProductionState.DRAFT)


def test_round_up_to_pack_ceils_to_multiple() -> None:
    assert round_up_to_pack(D("161"), D("50")) == D("200")


def test_round_up_to_pack_exact_multiple_unchanged() -> None:
    assert round_up_to_pack(D("200"), D("50")) == D("200")


def test_round_up_to_pack_none_leaves_unchanged() -> None:
    assert round_up_to_pack(D("161.3"), None) == D("161.3")


def test_round_up_to_pack_rejects_non_positive_pack_size() -> None:
    with pytest.raises(ValueError):
        round_up_to_pack(D("10"), D("0"))


def test_quantize_display_rounds_half_up() -> None:
    assert quantize_display(D("1.005")) == D("1.01")
    assert quantize_display(D("1.004")) == D("1.00")
