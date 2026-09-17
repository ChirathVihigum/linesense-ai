"""Inventory ledger commands, locked reservations, material readiness and
the material overview (task-8-brief.md).

Invariants maintained in the caller's transaction by every command here:

* `material_balances.on_hand_accepted` equals the sum of the factory's
  ledger movements on ACCEPTED lots for that material;
* `material_balances.reserved` equals the sum of ACTIVE reservations;
* `0 <= reserved <= on_hand_accepted` (also a database check constraint).

Lock order (shared with `app.domain.capacity.service` and the approval apply
path): affected orders -> `line_capacity_slots` -> `material_balances` ->
`material_lots`/`reservations`, each group ``FOR UPDATE`` in ascending id
order. Storekeeper commands lock the open orders whose BOM uses the material
*before* the balance row so the in-transaction `material_state` recompute
never waits on an order row while holding a balance lock.

`reserve_material` is the internal locked primitive (no principal, no
material-state recompute: approvals lock their own order first and refresh
other orders asynchronously); `create_reservation` is the storekeeper
command that wraps it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
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
    ExpectedReceipt,
    Factory,
    Material,
    MaterialBalance,
    MaterialLot,
    Order,
    Reservation,
    StockMovement,
)
from app.domain.clock import today_in, utcnow
from app.domain.inventory.calc import (
    UnsupportedUnitConversion,
    available_now,
    average_daily_consumption,
    convert_quantity,
    coverage_days,
    gross_demand,
    material_state,
    projected_balance,
    reorder_point,
    shortage,
)
from app.domain.rounding import quantize_display
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    ExpectedReceiptStatus,
    MaterialLotStatus,
    MaterialState,
    MovementType,
    ProductionState,
    ReservationStatus,
)

STATUS_SOURCE = "Calculated from records"
CONSUMPTION_WINDOW_DAYS = 14
MATERIAL_STATE_ORDER_LIMIT = 500

# Orders whose `material_state` is recomputed when a material changes.
MATERIAL_STATE_ORDER_STATES = (
    ProductionState.DRAFT.value,
    ProductionState.VALIDATED.value,
    ProductionState.PLANNED.value,
)
# Orders that may hold a manual reservation.
RESERVABLE_ORDER_STATES = (*MATERIAL_STATE_ORDER_STATES, ProductionState.IN_PRODUCTION.value)

_QUANTITY = Decimal("0.0001")
_SEVERITY = {
    MaterialState.READY: 0,
    MaterialState.AT_RISK: 1,
    MaterialState.SHORTAGE: 2,
    MaterialState.UNKNOWN: 3,
}


@dataclass(frozen=True)
class MaterialStatusRow:
    material_id: uuid.UUID
    material_code: str
    material_name: str
    unit: str
    on_hand: Decimal
    reserved: Decimal
    available_now: Decimal
    open_receipt_quantity: Decimal
    next_receipt_date: date | None
    average_daily_consumption: Decimal
    coverage_days: Decimal | None
    reorder_point: Decimal
    below_reorder_point: bool
    balance_version: int | None
    status_source: str = STATUS_SOURCE


@dataclass(frozen=True)
class LedgerRow:
    movement: StockMovement
    lot_code: str | None


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


async def scoped_material(
    session: AsyncSession, principal: Principal, factory_id: uuid.UUID, material_id: uuid.UUID
) -> Material:
    """A material addressed by path: 404 unless it belongs to the principal's org."""
    await load_scoped(session, Factory, factory_id, principal, "inventory:read")
    material = await session.get(Material, material_id)
    if material is None or material.organization_id != principal.organization_id:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
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


def _orders_using_materials(factory_id: uuid.UUID, material_ids: Collection[uuid.UUID]) -> Any:
    uses_material = sa.exists().where(
        BomLine.bom_version_id == Order.bom_version_id,
        BomLine.material_id.in_(list(material_ids)),
    )
    return (
        select(Order.id)
        .where(
            Order.factory_id == factory_id,
            Order.production_state.in_(MATERIAL_STATE_ORDER_STATES),
            uses_material,
        )
        .order_by(Order.due_date, Order.id)
        .limit(MATERIAL_STATE_ORDER_LIMIT)
    )


async def _lock_orders(session: AsyncSession, order_ids: Iterable[uuid.UUID]) -> list[Order]:
    ids = sorted(set(order_ids))
    if not ids:
        return []
    return list(
        (
            await session.scalars(
                select(Order)
                .where(Order.id.in_(ids))
                .order_by(Order.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def lock_orders_using_materials(
    session: AsyncSession, factory_id: uuid.UUID, material_ids: Collection[uuid.UUID]
) -> list[Order]:
    """Lock (ascending id) the open orders whose BOM uses any of ``material_ids``.

    Bounded to `MATERIAL_STATE_ORDER_LIMIT` orders (earliest due first).
    """
    if not material_ids:
        return []
    ids = (await session.scalars(_orders_using_materials(factory_id, material_ids))).all()
    return await _lock_orders(session, ids)


# --------------------------------------------------------------------------
# Material readiness
# --------------------------------------------------------------------------


def _combine(states: Iterable[MaterialState]) -> MaterialState:
    return max(states, key=lambda state: _SEVERITY[state], default=MaterialState.UNKNOWN)


async def recompute_material_states(
    session: AsyncSession,
    factory_id: uuid.UUID,
    material_ids: Collection[uuid.UUID],
    *,
    order_ids: Collection[uuid.UUID] | None = None,
) -> dict[uuid.UUID, MaterialState]:
    """Recompute `orders.material_state` for DRAFT/VALIDATED/PLANNED orders
    of ``factory_id`` whose BOM uses any of ``material_ids`` (optionally
    restricted to ``order_ids``), at most 500 orders, earliest due first.

    Per BOM line, with the order's remaining units (`quantity -
    produced_units`): demand = `gross_demand`; the order may use the
    factory's `available_now` plus its own ACTIVE reservations of that
    material; open expected receipts dated on or before the due date count
    toward the projection. `material_state` (Task 4) decides each line;
    the order takes the most severe line state. A missing balance, a missing
    BOM, or an unapproved unit conversion yields `UNKNOWN`. Orders whose
    state changes get `version + 1`. Locks the orders (ascending id); the
    storekeeper commands already hold those locks from before their balance
    lock, so this does not wait.

    Returns the new state of every order that was evaluated.
    """
    factory = await session.get(Factory, factory_id)
    if factory is None or not material_ids:
        return {}
    stmt = _orders_using_materials(factory_id, material_ids)
    if order_ids is not None:
        if not order_ids:
            return {}
        stmt = stmt.where(Order.id.in_(list(order_ids)))
    orders = [
        order
        for order in await _lock_orders(session, (await session.scalars(stmt)).all())
        if order.production_state in MATERIAL_STATE_ORDER_STATES
    ]
    if not orders:
        return {}

    as_of = today_in(factory.timezone)
    bom_ids = {order.bom_version_id for order in orders}
    lines_by_bom: dict[uuid.UUID, list[BomLine]] = defaultdict(list)
    for line in (
        await session.scalars(select(BomLine).where(BomLine.bom_version_id.in_(bom_ids)))
    ).all():
        lines_by_bom[line.bom_version_id].append(line)
    used_material_ids = {line.material_id for lines in lines_by_bom.values() for line in lines}

    materials = {
        material.id: material
        for material in (
            await session.scalars(select(Material).where(Material.id.in_(used_material_ids)))
        ).all()
    }
    balances = {
        balance.material_id: balance
        for balance in (
            await session.scalars(
                select(MaterialBalance).where(
                    MaterialBalance.factory_id == factory_id,
                    MaterialBalance.material_id.in_(used_material_ids),
                )
            )
        ).all()
    }
    own_reserved: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    for order_id, material_id, total in (
        await session.execute(
            select(Reservation.order_id, Reservation.material_id, sa.func.sum(Reservation.quantity))
            .where(
                Reservation.order_id.in_([order.id for order in orders]),
                Reservation.status == ReservationStatus.ACTIVE.value,
            )
            .group_by(Reservation.order_id, Reservation.material_id)
        )
    ).all():
        own_reserved[(order_id, material_id)] = Decimal(total)
    receipts: dict[uuid.UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    for receipt in (
        await session.scalars(
            select(ExpectedReceipt).where(
                ExpectedReceipt.factory_id == factory_id,
                ExpectedReceipt.material_id.in_(used_material_ids),
                ExpectedReceipt.status == ExpectedReceiptStatus.OPEN.value,
            )
        )
    ).all():
        receipts[receipt.material_id].append((receipt.expected_date, receipt.quantity))

    results: dict[uuid.UUID, MaterialState] = {}
    for order in orders:
        lines = lines_by_bom.get(order.bom_version_id, [])
        remaining_units = Decimal(max(0, order.quantity - order.produced_units))
        line_states: list[MaterialState] = []
        for line in lines:
            material = materials[line.material_id]
            balance = balances.get(line.material_id)
            try:
                per_unit = convert_quantity(line.quantity_per_unit, line.unit, material.unit)
            except UnsupportedUnitConversion:
                line_states.append(MaterialState.UNKNOWN)
                continue
            if balance is None:
                line_states.append(MaterialState.UNKNOWN)
                continue
            demand = gross_demand(remaining_units, per_unit, line.wastage_fraction)
            usable = available_now(balance.on_hand_accepted, balance.reserved) + own_reserved.get(
                (order.id, line.material_id), Decimal(0)
            )
            projected = projected_balance(
                available_now=usable,
                receipts=receipts.get(line.material_id, []),
                demand=[(as_of, demand)],
                at=order.due_date,
            )
            line_states.append(
                material_state(
                    shortage(usable, demand),
                    max(Decimal(0), -projected),
                    data_complete=True,
                )
            )
        state = _combine(line_states)
        results[order.id] = state
        if order.material_state != state.value:
            order.material_state = state.value
            order.version += 1
    await session.flush()
    return results


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

    await lock_orders_using_materials(session, factory.id, [material.id])
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
    await recompute_material_states(session, factory.id, [material.id])
    return movement


async def accept_lot(session: AsyncSession, principal: Principal, lot_id: uuid.UUID) -> MaterialLot:
    """Accept a QUARANTINE lot: its net ledger quantity joins `on_hand_accepted`."""
    lot = await load_scoped(session, MaterialLot, lot_id, principal, "inventory:write")
    await lock_orders_using_materials(session, lot.factory_id, [lot.material_id])
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
    await recompute_material_states(session, lot.factory_id, [lot.material_id])
    return lot


async def _consume_reservations(
    session: AsyncSession,
    *,
    order: Order,
    material_id: uuid.UUID,
    quantity: Decimal,
    actor_user_id: uuid.UUID,
) -> Decimal:
    """Consume up to ``quantity`` of the order's ACTIVE reservations of the
    material (oldest first). A partially consumed reservation keeps the
    remainder ACTIVE and a new CONSUMED row records the consumed part.
    Returns the consumed quantity."""
    reservations = (
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
    return quantity - remaining


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

    await lock_orders_using_materials(session, factory.id, [material.id])
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
    consumed = Decimal(0)
    if order is not None:
        consumed = await _consume_reservations(
            session,
            order=order,
            material_id=material.id,
            quantity=quantity,
            actor_user_id=principal.user_id,
        )
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
    await recompute_material_states(session, factory.id, [material.id])
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

    await lock_orders_using_materials(session, original.factory_id, [original.material_id])
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
    await recompute_material_states(session, original.factory_id, [original.material_id])
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
    if order.production_state not in RESERVABLE_ORDER_STATES:
        raise AppError(
            409, "CONFLICT", f"Cannot reserve material for a {order.production_state} order."
        )
    await lock_orders_using_materials(session, factory.id, [material.id])
    reservation = await reserve_material(
        session,
        organization_id=factory.organization_id,
        factory_id=factory.id,
        material_id=material.id,
        order_id=order.id,
        quantity=quantity,
        actor_user_id=principal.user_id,
    )
    await recompute_material_states(session, factory.id, [material.id])
    return reservation


async def release_reservation(
    session: AsyncSession, principal: Principal, reservation_id: uuid.UUID
) -> Reservation:
    """Release an ACTIVE reservation (storekeeper)."""
    reservation = await load_scoped(
        session, Reservation, reservation_id, principal, "inventory:write"
    )
    await lock_orders_using_materials(session, reservation.factory_id, [reservation.material_id])
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
    await recompute_material_states(session, reservation.factory_id, [reservation.material_id])
    return reservation


async def release_order_reservations(session: AsyncSession, order: Order) -> list[Reservation]:
    """Release every ACTIVE reservation of ``order`` under balance row locks.

    Used when an order is cancelled; the caller audits the cancellation.
    """
    reservations = list(
        (
            await session.scalars(
                select(Reservation)
                .where(
                    Reservation.order_id == order.id,
                    Reservation.status == ReservationStatus.ACTIVE.value,
                )
                .order_by(Reservation.id)
            )
        ).all()
    )
    if not reservations:
        return []
    material_ids = {reservation.material_id for reservation in reservations}
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
    for reservation in reservations:
        balance = balances.get(reservation.material_id)
        if balance is None:
            raise RuntimeError(f"active reservation {reservation.id} has no material balance")
        balance.reserved -= reservation.quantity
        _bump(balance)
        reservation.status = ReservationStatus.RELEASED.value
    await session.flush()
    return reservations


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


async def list_ledger(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    material_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[LedgerRow], int]:
    """A material's movements in the factory, newest first."""
    material = await scoped_material(session, principal, factory_id, material_id)
    conditions = [
        StockMovement.factory_id == factory_id,
        StockMovement.material_id == material.id,
    ]
    total = await session.scalar(
        select(sa.func.count()).select_from(StockMovement).where(*conditions)
    )
    rows = (
        await session.execute(
            select(StockMovement, MaterialLot.lot_code)
            .outerjoin(MaterialLot, MaterialLot.id == StockMovement.lot_id)
            .where(*conditions)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [LedgerRow(movement=m, lot_code=code) for m, code in rows], int(total or 0)


async def list_reservations(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    *,
    order_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[Reservation], int]:
    await load_scoped(session, Factory, factory_id, principal, "inventory:read")
    conditions: list[Any] = [Reservation.factory_id == factory_id]
    if order_id is not None:
        conditions.append(Reservation.order_id == order_id)
    total = await session.scalar(
        select(sa.func.count()).select_from(Reservation).where(*conditions)
    )
    rows = (
        await session.scalars(
            select(Reservation)
            .where(*conditions)
            .order_by(Reservation.created_at.desc(), Reservation.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(rows), int(total or 0)


async def material_overview(
    session: AsyncSession, factory_id: uuid.UUID, as_of: date
) -> list[MaterialStatusRow]:
    """Per material of the factory's organization (by code): stock position,
    open receipts, 14-day average consumption, coverage and reorder point.

    Consumption is the ISSUE movements whose factory-local date lies in
    `(as_of - 14 days, as_of]`. Coverage is `None` (unknown) without
    consumption. Averages/coverage are rounded for display only.
    """
    factory = await session.get(Factory, factory_id)
    if factory is None:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    materials = (
        await session.scalars(
            select(Material)
            .where(Material.organization_id == factory.organization_id)
            .order_by(Material.code)
        )
    ).all()
    balances = {
        balance.material_id: balance
        for balance in (
            await session.scalars(
                select(MaterialBalance).where(MaterialBalance.factory_id == factory_id)
            )
        ).all()
    }
    open_receipts: dict[uuid.UUID, tuple[Decimal, date]] = {
        material_id: (Decimal(total), first_date)
        for material_id, total, first_date in (
            await session.execute(
                select(
                    ExpectedReceipt.material_id,
                    sa.func.sum(ExpectedReceipt.quantity),
                    sa.func.min(ExpectedReceipt.expected_date),
                )
                .where(
                    ExpectedReceipt.factory_id == factory_id,
                    ExpectedReceipt.status == ExpectedReceiptStatus.OPEN.value,
                )
                .group_by(ExpectedReceipt.material_id)
            )
        ).all()
    }

    local_date = sa.cast(sa.func.timezone(factory.timezone, StockMovement.created_at), sa.Date)
    issues: dict[uuid.UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    for material_id, issued_on, total in (
        await session.execute(
            select(StockMovement.material_id, local_date, sa.func.sum(StockMovement.quantity))
            .where(
                StockMovement.factory_id == factory_id,
                StockMovement.movement_type == MovementType.ISSUE.value,
                StockMovement.created_at
                >= sa.func.timezone(
                    factory.timezone,
                    sa.cast(as_of - timedelta(days=CONSUMPTION_WINDOW_DAYS + 1), sa.DateTime),
                ),
            )
            .group_by(StockMovement.material_id, local_date)
        )
    ).all():
        issues[material_id].append((issued_on, Decimal(total)))

    rows: list[MaterialStatusRow] = []
    for material in materials:
        balance = balances.get(material.id)
        on_hand = balance.on_hand_accepted if balance is not None else Decimal(0)
        reserved = balance.reserved if balance is not None else Decimal(0)
        available = available_now(on_hand, reserved)
        daily = average_daily_consumption(
            issues.get(material.id, []), CONSUMPTION_WINDOW_DAYS, as_of
        )
        coverage = coverage_days(available, daily)
        rop = reorder_point(daily, material.lead_time_days, material.safety_stock)
        receipt_total, next_date = open_receipts.get(material.id, (Decimal(0), None))
        rows.append(
            MaterialStatusRow(
                material_id=material.id,
                material_code=material.code,
                material_name=material.name,
                unit=material.unit,
                on_hand=on_hand,
                reserved=reserved,
                available_now=available,
                open_receipt_quantity=receipt_total,
                next_receipt_date=next_date,
                average_daily_consumption=quantize_display(daily, 4),
                coverage_days=quantize_display(coverage, 2) if coverage is not None else None,
                reorder_point=quantize_display(rop, 4),
                below_reorder_point=available < rop,
                balance_version=balance.version if balance is not None else None,
            )
        )
    return rows
