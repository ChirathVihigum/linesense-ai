"""Inventory ledger commands and locked reservations (task-8-brief.md).

Material readiness lives in `app.domain.inventory.readiness` and read
models in `app.domain.inventory.queries`; both are re-exported here.

Invariants maintained in the caller's transaction by every command here:

* `material_balances.on_hand_accepted` equals the sum of the factory's
  ledger movements on ACCEPTED lots for that material;
* `material_balances.reserved` equals the sum of ACTIVE reservations;
* `0 <= reserved <= on_hand_accepted` (also a database check constraint).

Lock order (shared with `app.domain.capacity.service` and the approval apply
path): affected orders -> `line_capacity_slots` -> `material_balances` ->
`material_lots`/`reservations`, each group ``FOR UPDATE`` in ascending id
order. Storekeeper commands lock the open orders whose BOM uses the material
*before* the balance row and pass exactly those ids to
`recompute_material_states`, so no order row is locked while a balance lock
is held.

`reserve_material` is the internal locked primitive (no principal, no
material-state recompute: approvals lock their own order first and refresh
other orders asynchronously); `create_reservation` is the storekeeper
command that wraps it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    BomLine,
    Factory,
    Material,
    MaterialBalance,
    MaterialLot,
    Order,
    Reservation,
    StockMovement,
)
from app.domain.clock import utcnow
from app.domain.inventory.calc import available_now
from app.domain.inventory.queries import (
    CONSUMPTION_WINDOW_DAYS,
    STATUS_SOURCE,
    LedgerRow,
    MaterialStatusRow,
    list_ledger,
    list_reservations,
    material_overview,
    scoped_material,
)
from app.domain.inventory.readiness import (
    MATERIAL_STATE_ORDER_LIMIT,
    MATERIAL_STATE_ORDER_STATES,
    lock_orders_using_materials,
    recompute_material_states,
)
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    MaterialLotStatus,
    MovementType,
    ProductionState,
    ReservationStatus,
)

# Re-exported so callers keep importing everything from this module.
__all__ = [
    "CONSUMPTION_WINDOW_DAYS",
    "MATERIAL_STATE_ORDER_LIMIT",
    "MATERIAL_STATE_ORDER_STATES",
    "STATUS_SOURCE",
    "LedgerRow",
    "MaterialStatusRow",
    "accept_lot",
    "create_reservation",
    "ensure_balance",
    "get_balance",
    "list_ledger",
    "list_reservations",
    "lock_balances",
    "lock_orders_using_materials",
    "material_overview",
    "recompute_material_states",
    "record_correction",
    "record_issue",
    "record_receipt",
    "release_order_reservations",
    "release_reservation",
    "reserve_material",
    "scoped_material",
]

# Orders that may hold a manual reservation.
RESERVABLE_ORDER_STATES = (*MATERIAL_STATE_ORDER_STATES, ProductionState.IN_PRODUCTION.value)

_QUANTITY = Decimal("0.0001")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _quantity(value: Decimal, field: str) -> Decimal:
    """Round to the ledger's numeric(14,4) precision and require > 0."""
    rounded = value.quantize(_QUANTITY, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Quantity must be greater than zero.",
            field_errors=[{"field": field, "message": "Must be greater than zero."}],
        )
    return rounded


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        422, "VALIDATION_ERROR", message, field_errors=[{"field": field, "message": message}]
    )


def _balance_snapshot(balance: MaterialBalance) -> dict[str, Any]:
    return {
        "material_id": str(balance.material_id),
        "on_hand_accepted": str(balance.on_hand_accepted),
        "reserved": str(balance.reserved),
        "version": balance.version,
    }


def _bump(balance: MaterialBalance) -> None:
    balance.version += 1


async def _audit(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    await record_audit(
        session,
        organization_id=organization_id,
        factory_id=factory_id,
        actor_type=(ActorType.USER if actor_user_id is not None else ActorType.SYSTEM).value,
        actor_id=str(actor_user_id) if actor_user_id is not None else "system",
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        outcome=AuditOutcome.SUCCESS.value,
        reason=reason,
        before=before,
        after=after,
    )


async def _org_material(
    session: AsyncSession, organization_id: uuid.UUID, material_id: uuid.UUID
) -> Material:
    material = await session.get(Material, material_id)
    if material is None or material.organization_id != organization_id:
        raise _invalid("material_id", "Unknown material.")
    return material


async def _lot_net(session: AsyncSession, lot_id: uuid.UUID) -> Decimal:
    total = await session.scalar(
        select(sa.func.coalesce(sa.func.sum(StockMovement.quantity), 0)).where(
            StockMovement.lot_id == lot_id
        )
    )
    return Decimal(total or 0)


async def _lock_lot(session: AsyncSession, lot_id: uuid.UUID) -> MaterialLot:
    lot = (
        await session.scalars(
            select(MaterialLot)
            .where(MaterialLot.id == lot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    return lot


# --------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------


async def ensure_balance(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID,
    material_id: uuid.UUID,
) -> uuid.UUID:
    """The id of the (factory, material) balance, creating it at version 1."""
    await session.execute(
        insert(MaterialBalance)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            factory_id=factory_id,
            material_id=material_id,
            on_hand_accepted=Decimal(0),
            reserved=Decimal(0),
            version=1,
        )
        .on_conflict_do_nothing(index_elements=["factory_id", "material_id"])
    )
    balance_id = await session.scalar(
        select(MaterialBalance.id).where(
            MaterialBalance.factory_id == factory_id,
            MaterialBalance.material_id == material_id,
        )
    )
    if balance_id is None:
        raise RuntimeError("material balance vanished right after being ensured")
    return balance_id


async def lock_balances(
    session: AsyncSession, balance_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, MaterialBalance]:
    """Lock ``balance_ids`` (``FOR UPDATE``, ascending id) and return fresh rows."""
    ids = sorted(set(balance_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(MaterialBalance)
            .where(MaterialBalance.id.in_(ids))
            .order_by(MaterialBalance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    return {row.id: row for row in rows}


async def _lock_balance_for(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID,
    material_id: uuid.UUID,
) -> MaterialBalance:
    balance_id = await ensure_balance(
        session, organization_id=organization_id, factory_id=factory_id, material_id=material_id
    )
    return (await lock_balances(session, [balance_id]))[balance_id]


async def get_balance(
    session: AsyncSession, factory_id: uuid.UUID, material_id: uuid.UUID
) -> MaterialBalance | None:
    balance: MaterialBalance | None = await session.scalar(
        select(MaterialBalance).where(
            MaterialBalance.factory_id == factory_id,
            MaterialBalance.material_id == material_id,
        )
    )
    return balance


# --------------------------------------------------------------------------
# Ledger commands
# --------------------------------------------------------------------------


async def record_receipt(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    *,
    material_id: uuid.UUID,
    lot_code: str,
    quantity: Decimal,
    accept: bool,
) -> StockMovement:
    """Receive ``quantity`` into lot ``lot_code`` (created when new).

    A new lot is ACCEPTED when ``accept`` else QUARANTINE. Receiving into an
    existing lot follows its status (``accept`` also accepts a quarantined
    lot); REJECTED lots and lots of another material are refused (409).
    """
    factory = await load_scoped(session, Factory, factory_id, principal, "inventory:write")
    quantity = _quantity(quantity, "quantity")
    material = await _org_material(session, principal.organization_id, material_id)

    locked_orders = await lock_orders_using_materials(session, factory.id, [material.id])
    balance = await _lock_balance_for(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        material_id=material.id,
    )
    before = _balance_snapshot(balance)

    await session.execute(
        insert(MaterialLot)
        .values(
            id=uuid.uuid4(),
            organization_id=factory.organization_id,
            factory_id=factory.id,
            material_id=material.id,
            lot_code=lot_code,
            status=(MaterialLotStatus.ACCEPTED if accept else MaterialLotStatus.QUARANTINE).value,
            received_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["factory_id", "lot_code"])
    )
    lot_id = await session.scalar(
        select(MaterialLot.id).where(
            MaterialLot.factory_id == factory.id, MaterialLot.lot_code == lot_code
        )
    )
    if lot_id is None:
        raise RuntimeError("material lot vanished right after being ensured")
    lot = await _lock_lot(session, lot_id)
    if lot.material_id != material.id:
        raise AppError(409, "CONFLICT", "This lot code belongs to a different material.")
    if lot.status == MaterialLotStatus.REJECTED.value:
        raise AppError(409, "CONFLICT", "Cannot receive into a rejected lot.")

    movement = StockMovement(
        organization_id=factory.organization_id,
        factory_id=factory.id,
        material_id=material.id,
        lot_id=lot.id,
        movement_type=MovementType.RECEIPT.value,
        quantity=quantity,
        created_by=principal.user_id,
    )
    session.add(movement)
    await session.flush()

    if lot.status == MaterialLotStatus.ACCEPTED.value:
        balance.on_hand_accepted += quantity
        _bump(balance)
    elif accept:
        balance.on_hand_accepted += await _lot_net(session, lot.id)
        lot.status = MaterialLotStatus.ACCEPTED.value
        _bump(balance)
    await session.flush()

    await _audit(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        actor_user_id=principal.user_id,
        action="inventory.receipt",
        target_type="stock_movement",
        target_id=movement.id,
        before=before,
        after={
            **_balance_snapshot(balance),
            "lot_id": str(lot.id),
            "lot_status": lot.status,
            "quantity": str(quantity),
        },
    )
    await recompute_material_states(session, factory.id, [material.id], order_ids=locked_orders)
    return movement


async def accept_lot(session: AsyncSession, principal: Principal, lot_id: uuid.UUID) -> MaterialLot:
    """Accept a QUARANTINE lot: its net ledger quantity joins `on_hand_accepted`."""
    lot = await load_scoped(session, MaterialLot, lot_id, principal, "inventory:write")
    locked_orders = await lock_orders_using_materials(session, lot.factory_id, [lot.material_id])
    balance = await _lock_balance_for(
        session,
        organization_id=lot.organization_id,
        factory_id=lot.factory_id,
        material_id=lot.material_id,
    )
    lot = await _lock_lot(session, lot.id)
    if lot.status != MaterialLotStatus.QUARANTINE.value:
        raise AppError(
            409, "CONFLICT", f"Only quarantined lots can be accepted (lot is {lot.status})."
        )
    before = _balance_snapshot(balance)
    net = await _lot_net(session, lot.id)
    lot.status = MaterialLotStatus.ACCEPTED.value
    if net != 0:
        balance.on_hand_accepted += net
        _bump(balance)
    await session.flush()

    await _audit(
        session,
        organization_id=lot.organization_id,
        factory_id=lot.factory_id,
        actor_user_id=principal.user_id,
        action="inventory.lot_accept",
        target_type="material_lot",
        target_id=lot.id,
        before={**before, "lot_status": MaterialLotStatus.QUARANTINE.value},
        after={**_balance_snapshot(balance), "lot_status": lot.status, "net_quantity": str(net)},
    )
    await recompute_material_states(
        session, lot.factory_id, [lot.material_id], order_ids=locked_orders
    )
    return lot


async def _lock_active_reservations(
    session: AsyncSession, *, order: Order, material_id: uuid.UUID
) -> list[Reservation]:
    """The order's ACTIVE reservations of the material, locked, oldest first."""
    return list(
        (
            await session.scalars(
                select(Reservation)
                .where(
                    Reservation.order_id == order.id,
                    Reservation.material_id == material_id,
                    Reservation.factory_id == order.factory_id,
                    Reservation.status == ReservationStatus.ACTIVE.value,
                )
                .order_by(Reservation.created_at, Reservation.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def _consume_reservations(
    session: AsyncSession,
    reservations: list[Reservation],
    *,
    quantity: Decimal,
    actor_user_id: uuid.UUID,
) -> None:
    """Consume ``quantity`` (at most their total) from locked ACTIVE
    ``reservations`` in order. A partially consumed reservation keeps the
    remainder ACTIVE and a new CONSUMED row records the consumed part."""
    remaining = quantity
    for reservation in reservations:
        if remaining <= 0:
            break
        take = min(reservation.quantity, remaining)
        if take == reservation.quantity:
            reservation.status = ReservationStatus.CONSUMED.value
        else:
            reservation.quantity -= take
            session.add(
                Reservation(
                    organization_id=reservation.organization_id,
                    factory_id=reservation.factory_id,
                    material_id=reservation.material_id,
                    order_id=reservation.order_id,
                    quantity=take,
                    status=ReservationStatus.CONSUMED.value,
                    recommendation_id=reservation.recommendation_id,
                    created_by=actor_user_id,
                )
            )
        remaining -= take


async def record_issue(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    *,
    material_id: uuid.UUID,
    lot_id: uuid.UUID,
    quantity: Decimal,
    order_id: uuid.UUID | None,
    reason: str | None,
) -> StockMovement:
    """Issue ``quantity`` (positive) from an ACCEPTED lot.

    Refused (409) when the lot holds less, or when the issue would make
    `on_hand_accepted - reserved` negative after consuming the order's own
    reservations of the material (if ``order_id`` is given).
    """
    factory = await load_scoped(session, Factory, factory_id, principal, "inventory:write")
    quantity = _quantity(quantity, "quantity")
    material = await _org_material(session, principal.organization_id, material_id)
    lot = await session.get(MaterialLot, lot_id)
    if lot is None or lot.factory_id != factory.id or lot.material_id != material.id:
        raise _invalid("lot_id", "Unknown lot for this material and factory.")
    order: Order | None = None
    if order_id is not None:
        order = await session.get(Order, order_id)
        if order is None or order.factory_id != factory.id:
            raise _invalid("order_id", "Unknown order for this factory.")

    locked_orders = await lock_orders_using_materials(
        session,
        factory.id,
        [material.id],
        extra_order_ids=[order.id] if order is not None else (),
    )
    balance = await _lock_balance_for(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        material_id=material.id,
    )
    lot = await _lock_lot(session, lot.id)
    if lot.status != MaterialLotStatus.ACCEPTED.value:
        raise AppError(409, "CONFLICT", "Only accepted lots can be issued.")
    lot_available = await _lot_net(session, lot.id)
    if lot_available < quantity:
        raise AppError(
            409,
            "CONFLICT",
            f"Lot {lot.lot_code} holds only {lot_available}.",
            field_errors=[{"field": "quantity", "message": "Exceeds the lot's quantity."}],
        )

    before = _balance_snapshot(balance)
    own_reservations: list[Reservation] = []
    if order is not None:
        own_reservations = await _lock_active_reservations(
            session, order=order, material_id=material.id
        )
    consumed = min(quantity, sum((r.quantity for r in own_reservations), Decimal(0)))
    before_available = available_now(balance.on_hand_accepted, balance.reserved)
    new_on_hand = balance.on_hand_accepted - quantity
    new_reserved = balance.reserved - consumed
    if available_now(new_on_hand, new_reserved) < 0:
        raise AppError(
            409,
            "CONFLICT",
            "Insufficient available material",
            field_errors=[
                {
                    "field": "quantity",
                    "message": (
                        f"Available now is {before_available}"
                        f" (plus {consumed} reserved for this order)."
                    ),
                }
            ],
        )

    # Validated above; only now mutate the reservations.
    _consume_reservations(
        session, own_reservations, quantity=consumed, actor_user_id=principal.user_id
    )
    movement = StockMovement(
        organization_id=factory.organization_id,
        factory_id=factory.id,
        material_id=material.id,
        lot_id=lot.id,
        movement_type=MovementType.ISSUE.value,
        quantity=-quantity,
        order_id=order.id if order is not None else None,
        reason=reason,
        created_by=principal.user_id,
    )
    session.add(movement)
    balance.on_hand_accepted = new_on_hand
    balance.reserved = new_reserved
    _bump(balance)
    await session.flush()

    await _audit(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        actor_user_id=principal.user_id,
        action="inventory.issue",
        target_type="stock_movement",
        target_id=movement.id,
        reason=reason,
        before=before,
        after={
            **_balance_snapshot(balance),
            "lot_id": str(lot.id),
            "quantity": str(-quantity),
            "order_id": str(order.id) if order is not None else None,
            "consumed_reservation_quantity": str(consumed),
        },
    )
    await recompute_material_states(session, factory.id, [material.id], order_ids=locked_orders)
    return movement


async def record_correction(
    session: AsyncSession,
    principal: Principal,
    *,
    movement_id: uuid.UUID,
    quantity_delta: Decimal,
    reason: str,
) -> StockMovement:
    """Correct a RECEIPT or ISSUE by a signed ``quantity_delta``.

    The correction references the original movement and its lot. It may not
    make the lot's net quantity negative, and on an ACCEPTED lot it may not
    leave `on_hand_accepted` below `reserved` (409).
    """
    original = await load_scoped(session, StockMovement, movement_id, principal, "inventory:write")
    if not reason or not reason.strip():
        raise _invalid("reason", "A reason is required for corrections.")
    delta = quantity_delta.quantize(_QUANTITY, rounding=ROUND_HALF_UP)
    if delta == 0:
        raise _invalid("quantity_delta", "The correction must change the quantity.")
    if original.movement_type == MovementType.CORRECTION.value:
        raise _invalid("movement_id", "Correct the original receipt or issue, not a correction.")
    if original.lot_id is None:
        raise _invalid("movement_id", "The movement has no lot to correct.")

    locked_orders = await lock_orders_using_materials(
        session, original.factory_id, [original.material_id]
    )
    balance = await _lock_balance_for(
        session,
        organization_id=original.organization_id,
        factory_id=original.factory_id,
        material_id=original.material_id,
    )
    lot = await _lock_lot(session, original.lot_id)
    if await _lot_net(session, lot.id) + delta < 0:
        raise AppError(409, "CONFLICT", "The correction would make the lot's quantity negative.")

    before = _balance_snapshot(balance)
    if lot.status == MaterialLotStatus.ACCEPTED.value:
        new_on_hand = balance.on_hand_accepted + delta
        if new_on_hand < balance.reserved:
            raise AppError(
                409,
                "CONFLICT",
                "The correction would leave less stock on hand than is reserved.",
                field_errors=[
                    {
                        "field": "quantity_delta",
                        "message": (
                            f"On hand {balance.on_hand_accepted}, reserved {balance.reserved}."
                        ),
                    }
                ],
            )
        balance.on_hand_accepted = new_on_hand
        _bump(balance)

    movement = StockMovement(
        organization_id=original.organization_id,
        factory_id=original.factory_id,
        material_id=original.material_id,
        lot_id=lot.id,
        movement_type=MovementType.CORRECTION.value,
        quantity=delta,
        corrects_movement_id=original.id,
        reason=reason,
        order_id=original.order_id,
        created_by=principal.user_id,
    )
    session.add(movement)
    await session.flush()

    await _audit(
        session,
        organization_id=original.organization_id,
        factory_id=original.factory_id,
        actor_user_id=principal.user_id,
        action="inventory.correction",
        target_type="stock_movement",
        target_id=movement.id,
        reason=reason,
        before=before,
        after={
            **_balance_snapshot(balance),
            "corrects_movement_id": str(original.id),
            "quantity_delta": str(delta),
        },
    )
    await recompute_material_states(
        session, original.factory_id, [original.material_id], order_ids=locked_orders
    )
    return movement


# --------------------------------------------------------------------------
# Reservations
# --------------------------------------------------------------------------


async def reserve_material(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID,
    material_id: uuid.UUID,
    order_id: uuid.UUID,
    quantity: Decimal,
    actor_user_id: uuid.UUID | None,
    recommendation_id: uuid.UUID | None = None,
) -> Reservation:
    """Reserve ``quantity`` of a material for an order under the balance lock.

    Internal primitive: no authorization and no `material_state` recompute
    (callers do both). Raises 409 ``CONFLICT`` "Insufficient available
    material" when `on_hand_accepted - reserved < quantity`.
    """
    quantity = _quantity(quantity, "quantity")
    balance = await _lock_balance_for(
        session,
        organization_id=organization_id,
        factory_id=factory_id,
        material_id=material_id,
    )
    available = available_now(balance.on_hand_accepted, balance.reserved)
    if available < quantity:
        raise AppError(
            409,
            "CONFLICT",
            "Insufficient available material",
            field_errors=[{"field": "quantity", "message": f"Available now is {available}."}],
        )
    before = _balance_snapshot(balance)
    reservation = Reservation(
        organization_id=organization_id,
        factory_id=factory_id,
        material_id=material_id,
        order_id=order_id,
        quantity=quantity,
        status=ReservationStatus.ACTIVE.value,
        recommendation_id=recommendation_id,
        created_by=actor_user_id,
    )
    session.add(reservation)
    balance.reserved += quantity
    _bump(balance)
    await session.flush()

    await _audit(
        session,
        organization_id=organization_id,
        factory_id=factory_id,
        actor_user_id=actor_user_id,
        action="inventory.reserve",
        target_type="reservation",
        target_id=reservation.id,
        before=before,
        after={
            **_balance_snapshot(balance),
            "order_id": str(order_id),
            "quantity": str(quantity),
            "recommendation_id": str(recommendation_id) if recommendation_id else None,
        },
    )
    return reservation


async def create_reservation(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    *,
    material_id: uuid.UUID,
    order_id: uuid.UUID,
    quantity: Decimal,
) -> Reservation:
    """Storekeeper-created (manual) reservation."""
    factory = await load_scoped(session, Factory, factory_id, principal, "inventory:write")
    material = await _org_material(session, principal.organization_id, material_id)
    order = await session.get(Order, order_id)
    if order is None or order.factory_id != factory.id:
        raise _invalid("order_id", "Unknown order for this factory.")
    on_bom = await session.scalar(
        select(
            sa.exists().where(
                BomLine.bom_version_id == order.bom_version_id,
                BomLine.material_id == material.id,
            )
        )
    )
    if not on_bom:
        raise _invalid("material_id", "The material is not on the order's bill of materials.")
    locked_orders = await lock_orders_using_materials(
        session, factory.id, [material.id], extra_order_ids=[order.id]
    )
    # Re-check under the order lock: a concurrent cancel may have committed.
    if order.production_state not in RESERVABLE_ORDER_STATES:
        raise AppError(
            409, "CONFLICT", f"Cannot reserve material for a {order.production_state} order."
        )
    reservation = await reserve_material(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        material_id=material.id,
        order_id=order.id,
        quantity=quantity,
        actor_user_id=principal.user_id,
    )
    await recompute_material_states(session, factory.id, [material.id], order_ids=locked_orders)
    return reservation


async def release_reservation(
    session: AsyncSession, principal: Principal, reservation_id: uuid.UUID
) -> Reservation:
    """Release an ACTIVE reservation (storekeeper)."""
    reservation = await load_scoped(
        session, Reservation, reservation_id, principal, "inventory:write"
    )
    locked_orders = await lock_orders_using_materials(
        session,
        reservation.factory_id,
        [reservation.material_id],
        extra_order_ids=[reservation.order_id],
    )
    balance = await _lock_balance_for(
        session,
        organization_id=reservation.organization_id,
        factory_id=reservation.factory_id,
        material_id=reservation.material_id,
    )
    reservation = (
        await session.scalars(
            select(Reservation)
            .where(Reservation.id == reservation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    if reservation.status != ReservationStatus.ACTIVE.value:
        raise AppError(
            409, "CONFLICT", f"Only active reservations can be released ({reservation.status})."
        )
    before = _balance_snapshot(balance)
    reservation.status = ReservationStatus.RELEASED.value
    balance.reserved -= reservation.quantity
    _bump(balance)
    await session.flush()

    await _audit(
        session,
        organization_id=reservation.organization_id,
        factory_id=reservation.factory_id,
        actor_user_id=principal.user_id,
        action="inventory.release",
        target_type="reservation",
        target_id=reservation.id,
        before=before,
        after={**_balance_snapshot(balance), "quantity": str(reservation.quantity)},
    )
    await recompute_material_states(
        session, reservation.factory_id, [reservation.material_id], order_ids=locked_orders
    )
    return reservation


async def release_order_reservations(session: AsyncSession, order: Order) -> list[Reservation]:
    """Release every ACTIVE reservation of ``order`` under balance row locks.

    Used when an order is cancelled (the caller holds the order row lock and
    audits the cancellation). The balances to lock are those of the order's
    BOM materials and of every reservation the order has ever had, locked in
    ascending id order; the ACTIVE reservations are then re-read ``FOR
    UPDATE`` so a concurrent release or consuming issue that committed first
    is never applied twice. An ACTIVE reservation whose balance was not in
    the locked set (a reservation for a new, non-BOM material created
    concurrently) aborts with 409 ``CONFLICT`` so the caller can retry,
    rather than locking out of order.
    """
    material_ids = set(
        (
            await session.scalars(
                select(BomLine.material_id).where(BomLine.bom_version_id == order.bom_version_id)
            )
        ).all()
    )
    material_ids.update(
        (
            await session.scalars(
                select(Reservation.material_id).where(Reservation.order_id == order.id)
            )
        ).all()
    )
    if not material_ids:
        return []
    balance_ids = (
        await session.scalars(
            select(MaterialBalance.id).where(
                MaterialBalance.factory_id == order.factory_id,
                MaterialBalance.material_id.in_(material_ids),
            )
        )
    ).all()
    balances = {
        balance.material_id: balance
        for balance in (await lock_balances(session, balance_ids)).values()
    }
    reservations = list(
        (
            await session.scalars(
                select(Reservation)
                .where(
                    Reservation.order_id == order.id,
                    Reservation.status == ReservationStatus.ACTIVE.value,
                )
                .order_by(Reservation.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    if any(reservation.material_id not in balances for reservation in reservations):
        raise AppError(
            409, "CONFLICT", "The order's reservations changed concurrently; please retry."
        )
    for reservation in reservations:
        balance = balances[reservation.material_id]
        balance.reserved -= reservation.quantity
        _bump(balance)
        reservation.status = ReservationStatus.RELEASED.value
    await session.flush()
    return reservations
