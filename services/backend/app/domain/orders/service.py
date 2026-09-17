"""Order commands and queries: list/create/detail, lifecycle transitions,
production progress, and shipment eligibility.

Database access lives here (unlike the pure `app.domain.orders.lifecycle`
and `app.domain.quality.calc` modules); every write happens inside the
caller's transaction (the `get_db_session` FastAPI dependency commits or
rolls back around the route handler).

Cancelling an order releases its active allocations and reservations
through the shared, row-locking helpers of `app.domain.capacity.service`
and `app.domain.inventory.service` (lock order: order -> slots ->
balances).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.policy import Principal, require
from app.auth.scope import load_scoped
from app.db.models import (
    Allocation,
    AnalysisRun,
    BomLine,
    BomVersion,
    Customer,
    Factory,
    Inspection,
    Material,
    Notification,
    Order,
    QualityHold,
    QualityPolicyVersion,
    QualityRelease,
    Reservation,
    Style,
    StyleOperation,
)
from app.domain.capacity.service import release_order_allocations
from app.domain.clock import today_in
from app.domain.inventory.service import release_order_reservations
from app.domain.orders.lifecycle import TRANSITIONS, InvalidTransition, get_transition
from app.domain.quality.calc import ShipmentEligibility, ShipmentFacts, shipment_eligibility
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    InspectionResult,
    InspectionType,
    MaterialState,
    OrderSource,
    PolicyStatus,
    ProductionState,
    QualityHoldStatus,
    QualityState,
    Role,
)

DEMO_POLICY_CODE = "QP-DEMO"

# `VALIDATED -> PLANNED` is a real transition in the lifecycle policy (it is
# how a run's applied recommendation moves an order to PLANNED), but it is
# never reachable through the `/orders/{id}/transitions` command endpoint:
# planning only happens through `recommendation:apply`.
_BLOCKED_TRANSITIONS: dict[tuple[ProductionState, ProductionState], str] = {
    (ProductionState.VALIDATED, ProductionState.PLANNED): (
        "Planning is applied through an approved recommendation."
    ),
}


@dataclass(frozen=True)
class NewOrderInput:
    external_ref: str
    customer_id: uuid.UUID
    style_id: uuid.UUID
    quantity: int
    due_date: date
    priority: int


@dataclass(frozen=True)
class OrderListRow:
    order: Order
    customer: Customer
    style: Style
    shipment: ShipmentEligibility


@dataclass(frozen=True)
class OrderDetailData:
    order: Order
    factory: Factory
    customer: Customer
    style: Style
    bom_version: BomVersion
    bom_lines: list[tuple[BomLine, Material]]
    operations: list[StyleOperation]
    allocations: list[Allocation]
    reservations: list[Reservation]
    inspections: list[Inspection]
    holds: list[QualityHold]
    latest_run: AnalysisRun | None
    shipment: ShipmentEligibility
    allowed_transitions: list[str]


def _order_snapshot(order: Order) -> dict[str, Any]:
    return {
        "production_state": order.production_state,
        "material_state": order.material_state,
        "quality_state": order.quality_state,
        "produced_units": order.produced_units,
        "packed_units": order.packed_units,
        "version": order.version,
    }


async def compute_shipment(session: AsyncSession, order: Order) -> ShipmentEligibility:
    """Build `ShipmentFacts` straight from the database and evaluate them.

    Task 9 will move this into the quality service; kept here for now
    because orders is the only caller until inspections/holds get their own
    write endpoints.
    """
    policy = await session.scalar(
        select(QualityPolicyVersion)
        .where(
            QualityPolicyVersion.organization_id == order.organization_id,
            QualityPolicyVersion.code == DEMO_POLICY_CODE,
            QualityPolicyVersion.status == PolicyStatus.ACTIVE.value,
        )
        .order_by(QualityPolicyVersion.version_no.desc())
        .limit(1)
    )
    policy_known = policy is not None
    required_types = (
        frozenset(cast("list[str]", policy.rules["required_inspection_types"]))
        if policy is not None
        else frozenset()
    )

    passed_types: set[str] = set()
    latest_final: Inspection | None = None
    for inspection_type in InspectionType:
        latest = await session.scalar(
            select(Inspection)
            .where(
                Inspection.order_id == order.id,
                Inspection.inspection_type == inspection_type.value,
            )
            .order_by(Inspection.inspected_at.desc(), Inspection.id.desc())
            .limit(1)
        )
        if latest is None:
            continue
        if inspection_type == InspectionType.FINAL:
            latest_final = latest
        if latest.result == InspectionResult.PASS_.value:
            passed_types.add(inspection_type.value)

    has_active_hold = bool(
        await session.scalar(
            select(
                sa.exists().where(
                    QualityHold.order_id == order.id,
                    QualityHold.status == QualityHoldStatus.ACTIVE.value,
                )
            )
        )
    )
    has_valid_release = False
    if latest_final is not None:
        has_valid_release = bool(
            await session.scalar(
                select(
                    sa.exists().where(
                        QualityRelease.order_id == order.id,
                        QualityRelease.inspection_id == latest_final.id,
                    )
                )
            )
        )

    facts = ShipmentFacts(
        production_state=order.production_state,
        quantity=order.quantity,
        packed_units=order.packed_units,
        passed_inspection_types=frozenset(passed_types),
        required_inspection_types=required_types,
        has_active_hold=has_active_hold,
        has_valid_release=has_valid_release,
        policy_known=policy_known,
    )
    return shipment_eligibility(facts)


async def list_orders(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    *,
    q: str | None = None,
    production_state: str | None = None,
    material_state: str | None = None,
    quality_state: str | None = None,
    due_before: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OrderListRow], int]:
    await load_scoped(session, Factory, factory_id, principal, "order:read")

    conditions: list[Any] = [Order.factory_id == factory_id]
    if production_state is not None:
        conditions.append(Order.production_state == production_state)
    if material_state is not None:
        conditions.append(Order.material_state == material_state)
    if quality_state is not None:
        conditions.append(Order.quality_state == quality_state)
    if due_before is not None:
        conditions.append(Order.due_date < due_before)
    if q:
        pattern = f"%{q}%"
        conditions.append(
            sa.or_(
                Order.external_ref.ilike(pattern),
                Customer.code.ilike(pattern),
                Customer.name.ilike(pattern),
            )
        )

    base = (
        select(Order, Customer, Style)
        .join(Customer, Customer.id == Order.customer_id)
        .join(Style, Style.id == Order.style_id)
        .where(*conditions)
    )
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = (
        await session.execute(
            base.order_by(Order.due_date, Order.external_ref).limit(limit).offset(offset)
        )
    ).all()

    result: list[OrderListRow] = []
    for order, customer, style in rows:
        shipment = await compute_shipment(session, order)
        result.append(OrderListRow(order=order, customer=customer, style=style, shipment=shipment))
    return result, int(total or 0)


async def create_order(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    data: NewOrderInput,
) -> Order:
    factory = await load_scoped(session, Factory, factory_id, principal, "order:create")

    customer = await session.get(Customer, data.customer_id)
    if customer is None or customer.organization_id != principal.organization_id:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Unknown customer.",
            field_errors=[{"field": "customer_id", "message": "Unknown customer."}],
        )
    style = await session.get(Style, data.style_id)
    if style is None or style.organization_id != principal.organization_id:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Unknown style.",
            field_errors=[{"field": "style_id", "message": "Unknown style."}],
        )
    bom_version = await session.scalar(
        select(BomVersion).where(BomVersion.style_id == style.id, BomVersion.is_active.is_(True))
    )
    if bom_version is None:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Style has no active bill of materials.",
            field_errors=[
                {"field": "style_id", "message": "Style has no active bill of materials."}
            ],
        )

    duplicate = await session.scalar(
        select(Order.id).where(
            Order.organization_id == principal.organization_id,
            Order.external_ref == data.external_ref,
        )
    )
    if duplicate is not None:
        raise AppError(
            409,
            "CONFLICT",
            "An order with this external reference already exists.",
            field_errors=[{"field": "external_ref", "message": "Already exists."}],
        )

    order = Order(
        organization_id=principal.organization_id,
        factory_id=factory.id,
        customer_id=customer.id,
        style_id=style.id,
        bom_version_id=bom_version.id,
        external_ref=data.external_ref,
        quantity=data.quantity,
        due_date=data.due_date,
        priority=data.priority,
        production_state=ProductionState.DRAFT.value,
        material_state=MaterialState.UNKNOWN.value,
        quality_state=QualityState.NOT_INSPECTED.value,
        source=OrderSource.MANUAL.value,
        created_by=principal.user_id,
    )
    session.add(order)
    await session.flush()

    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="order.create",
        target_type="order",
        target_id=str(order.id),
        outcome=AuditOutcome.SUCCESS.value,
        after=_order_snapshot(order),
    )
    return order


async def _order_data_incomplete_reason(session: AsyncSession, order: Order) -> str | None:
    bom_version = await session.get(BomVersion, order.bom_version_id)
    if bom_version is None or not bom_version.is_active:
        return "Order has no active bill of materials."
    if order.quantity <= 0:
        return "Order quantity must be greater than zero."
    factory = await session.get(Factory, order.factory_id)
    if factory is None:
        return "Order's factory could not be found."
    if order.due_date < today_in(factory.timezone):
        return "Due date must not be in the past."
    return None


async def _enforce_order_data_complete(session: AsyncSession, order: Order) -> None:
    reason = await _order_data_incomplete_reason(session, order)
    if reason is not None:
        raise AppError(409, "CONFLICT", reason)


async def _enforce_has_active_allocation(session: AsyncSession, order: Order) -> None:
    # Unreachable via the command endpoint: `VALIDATED -> PLANNED` is
    # rejected earlier by `_BLOCKED_TRANSITIONS`. Kept for completeness.
    raise AppError(
        409,
        "INVALID_TRANSITION",
        _BLOCKED_TRANSITIONS[(ProductionState.VALIDATED, ProductionState.PLANNED)],
    )


async def _enforce_produced_units_complete(session: AsyncSession, order: Order) -> None:
    if order.produced_units < order.quantity:
        raise AppError(
            409,
            "CONFLICT",
            "Produced units must reach the order quantity before completing production.",
        )


async def _enforce_shipment_eligible(session: AsyncSession, order: Order) -> None:
    eligibility = await compute_shipment(session, order)
    if not eligibility.eligible:
        raise AppError(
            409,
            "CONFLICT",
            "Order is not eligible for shipment.",
            field_errors=[
                {"field": "shipment", "message": reason} for reason in eligibility.reasons
            ],
        )


_ENFORCERS: dict[str, Callable[[AsyncSession, Order], Awaitable[None]]] = {
    "order_data_complete": _enforce_order_data_complete,
    "has_active_allocation": _enforce_has_active_allocation,
    "produced_units_complete": _enforce_produced_units_complete,
    "shipment_eligible": _enforce_shipment_eligible,
}


async def _notify_supervisors(
    session: AsyncSession, order: Order, *, kind: str, title: str, body: str
) -> None:
    """One role-targeted notification for the factory's supervisors."""
    session.add(
        Notification(
            organization_id=order.organization_id,
            factory_id=order.factory_id,
            user_id=None,
            role=Role.SUPERVISOR.value,
            kind=kind,
            title=title,
            body=body,
            link=f"/orders/{order.id}",
        )
    )
    await session.flush()


async def transition_order(
    session: AsyncSession,
    principal: Principal,
    order_id: uuid.UUID,
    target: str,
    expected_version: int,
    reason: str | None = None,
) -> Order:
    order = await load_scoped(session, Order, order_id, principal, "order:read")
    source_state = ProductionState(order.production_state)
    try:
        target_state = ProductionState(target)
    except ValueError as exc:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown production state {target!r}.") from exc

    blocked_message = _BLOCKED_TRANSITIONS.get((source_state, target_state))
    if blocked_message is not None:
        raise AppError(409, "INVALID_TRANSITION", blocked_message)
    try:
        rule = get_transition(source_state, target_state)
    except InvalidTransition as exc:
        raise AppError(409, "INVALID_TRANSITION", str(exc)) from exc

    require(principal, rule.permission, order.factory_id)

    if expected_version != order.version:
        raise AppError(409, "STALE_INPUT", "The order has changed since it was loaded.")

    for precondition in rule.preconditions:
        await _ENFORCERS[precondition](session, order)

    before = _order_snapshot(order)
    order.production_state = target_state.value
    order.version += 1

    if target_state == ProductionState.CANCELLED:
        await release_order_allocations(session, order)
        await release_order_reservations(session, order)

    await session.flush()
    after = _order_snapshot(order)

    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="order.transition",
        target_type="order",
        target_id=str(order.id),
        outcome=AuditOutcome.SUCCESS.value,
        reason=reason,
        before=before,
        after=after,
    )
    await _notify_supervisors(
        session,
        order,
        kind="order_transition",
        title=f"Order {order.external_ref} moved to {target_state.value}",
        body=reason
        or f"{principal.display_name} moved order {order.external_ref} to {target_state.value}.",
    )
    return order


async def progress_order(
    session: AsyncSession,
    principal: Principal,
    order_id: uuid.UUID,
    *,
    produced_units: int,
    packed_units: int,
    expected_version: int,
) -> Order:
    order = await load_scoped(session, Order, order_id, principal, "order:transition")

    if expected_version != order.version:
        raise AppError(409, "STALE_INPUT", "The order has changed since it was loaded.")

    field_errors: list[dict[str, str]] = []
    if produced_units < order.produced_units:
        field_errors.append(
            {"field": "produced_units", "message": "Produced units may only increase."}
        )
    if packed_units < order.packed_units:
        field_errors.append({"field": "packed_units", "message": "Packed units may only increase."})
    if produced_units > order.quantity:
        field_errors.append(
            {
                "field": "produced_units",
                "message": "Produced units may not exceed the order quantity.",
            }
        )
    if packed_units > order.quantity:
        field_errors.append(
            {"field": "packed_units", "message": "Packed units may not exceed the order quantity."}
        )
    if field_errors:
        raise AppError(409, "CONFLICT", "Invalid production progress.", field_errors=field_errors)

    before = _order_snapshot(order)
    order.produced_units = produced_units
    order.packed_units = packed_units
    order.version += 1
    await session.flush()
    after = _order_snapshot(order)

    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="order.progress",
        target_type="order",
        target_id=str(order.id),
        outcome=AuditOutcome.SUCCESS.value,
        before=before,
        after=after,
    )
    return order


def _allowed_transitions(principal: Principal, order: Order) -> list[str]:
    source_state = ProductionState(order.production_state)
    allowed: list[str] = []
    for (rule_source, rule_target), rule in TRANSITIONS.items():
        if rule_source != source_state:
            continue
        if (rule_source, rule_target) in _BLOCKED_TRANSITIONS:
            continue
        if principal.has(rule.permission, order.factory_id):
            allowed.append(rule_target.value)
    return sorted(allowed)


async def order_detail(
    session: AsyncSession, principal: Principal, order_id: uuid.UUID
) -> OrderDetailData:
    order = await load_scoped(session, Order, order_id, principal, "order:read")
    factory = await session.get(Factory, order.factory_id)
    customer = await session.get(Customer, order.customer_id)
    style = await session.get(Style, order.style_id)
    bom_version = await session.get(BomVersion, order.bom_version_id)
    if factory is None or customer is None or style is None or bom_version is None:
        raise RuntimeError("order references a missing factory, customer, style or BOM version")

    bom_line_rows = (
        await session.execute(
            select(BomLine, Material)
            .join(Material, Material.id == BomLine.material_id)
            .where(BomLine.bom_version_id == bom_version.id)
            .order_by(Material.code)
        )
    ).all()
    bom_lines = [(line, material) for line, material in bom_line_rows]

    operations = list(
        (
            await session.scalars(
                select(StyleOperation)
                .where(StyleOperation.style_id == style.id)
                .order_by(StyleOperation.sequence)
            )
        ).all()
    )
    allocations = list(
        (
            await session.scalars(
                select(Allocation)
                .where(Allocation.order_id == order.id)
                .order_by(Allocation.created_at)
            )
        ).all()
    )
    reservations = list(
        (
            await session.scalars(
                select(Reservation)
                .where(Reservation.order_id == order.id)
                .order_by(Reservation.created_at)
            )
        ).all()
    )
    inspections = list(
        (
            await session.scalars(
                select(Inspection)
                .where(Inspection.order_id == order.id)
                .order_by(Inspection.inspected_at.desc())
            )
        ).all()
    )
    holds = list(
        (
            await session.scalars(
                select(QualityHold)
                .where(QualityHold.order_id == order.id)
                .order_by(QualityHold.created_at.desc())
            )
        ).all()
    )
    latest_run = await session.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.order_id == order.id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(1)
    )
    shipment = await compute_shipment(session, order)
    allowed_transitions = _allowed_transitions(principal, order)

    return OrderDetailData(
        order=order,
        factory=factory,
        customer=customer,
        style=style,
        bom_version=bom_version,
        bom_lines=bom_lines,
        operations=operations,
        allocations=allocations,
        reservations=reservations,
        inspections=inspections,
        holds=holds,
        latest_run=latest_run,
        shipment=shipment,
        allowed_transitions=allowed_transitions,
    )
