"""Order lifecycle policy: the allowed `ProductionState` transitions, the
permission required to perform each one, and the preconditions the caller
must have already checked.

Pure data and lookup; no database access or I/O. Enforcing the
preconditions themselves (e.g. checking `has_active_allocation`) is the
caller's responsibility — this module only knows the policy shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.vocab import ProductionState


@dataclass(frozen=True)
class TransitionRule:
    source: ProductionState
    target: ProductionState
    permission: str
    preconditions: tuple[str, ...]


class InvalidTransition(ValueError):
    """Raised when no transition rule exists for a `(source, target)` pair."""


def _rule(
    source: ProductionState,
    target: ProductionState,
    permission: str,
    preconditions: tuple[str, ...],
) -> tuple[tuple[ProductionState, ProductionState], TransitionRule]:
    return (source, target), TransitionRule(
        source=source, target=target, permission=permission, preconditions=preconditions
    )


TRANSITIONS: Mapping[tuple[ProductionState, ProductionState], TransitionRule] = dict(
    (
        _rule(
            ProductionState.DRAFT,
            ProductionState.VALIDATED,
            "order:transition",
            ("order_data_complete",),
        ),
        _rule(
            ProductionState.VALIDATED,
            ProductionState.PLANNED,
            "recommendation:apply",
            ("has_active_allocation",),
        ),
        _rule(
            ProductionState.PLANNED,
            ProductionState.IN_PRODUCTION,
            "order:transition",
            (),
        ),
        _rule(
            ProductionState.IN_PRODUCTION,
            ProductionState.PRODUCTION_COMPLETE,
            "order:transition",
            ("produced_units_complete",),
        ),
        _rule(
            ProductionState.PRODUCTION_COMPLETE,
            ProductionState.DISPATCHED,
            "order:dispatch",
            ("shipment_eligible",),
        ),
        _rule(ProductionState.DRAFT, ProductionState.CANCELLED, "order:cancel", ()),
        _rule(ProductionState.VALIDATED, ProductionState.CANCELLED, "order:cancel", ()),
        _rule(ProductionState.PLANNED, ProductionState.CANCELLED, "order:cancel", ()),
    )
)


def get_transition(source: ProductionState, target: ProductionState) -> TransitionRule:
    """Look up the transition rule for `(source, target)`.

    Raises `InvalidTransition` when the pair is not an allowed transition.
    """
    rule = TRANSITIONS.get((source, target))
    if rule is None:
        raise InvalidTransition(f"no transition from {source} to {target}")
    return rule
