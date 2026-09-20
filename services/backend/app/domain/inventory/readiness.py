"""Order material readiness: locking the orders a material change affects
and recomputing their `material_state` (task-8-brief.md requirement 5).

Lock order (see `app.domain.inventory.service`): orders -> slots ->
balances. `lock_orders_using_materials` must therefore run *before* a
balance lock, and `recompute_material_states` must be given the ids it
returned (``order_ids=``) whenever a balance lock is already held, so that
it never locks an order row it was not given.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BomLine,
    ExpectedReceipt,
    Factory,
    Material,
    MaterialBalance,
    Order,
    Reservation,
)
from app.domain.clock import today_in
from app.domain.inventory.calc import (
    UnsupportedUnitConversion,
    available_now,
    convert_quantity,
    gross_demand,
    material_state,
    projected_balance,
    shortage,
)
from app.domain.vocab import (
    ExpectedReceiptStatus,
    MaterialState,
    ProductionState,
    ReservationStatus,
)

MATERIAL_STATE_ORDER_LIMIT = 500

# Orders whose `material_state` is recomputed when a material changes.
MATERIAL_STATE_ORDER_STATES = (
    ProductionState.DRAFT.value,
    ProductionState.VALIDATED.value,
    ProductionState.PLANNED.value,
)

_SEVERITY = {
    MaterialState.READY: 0,
    MaterialState.AT_RISK: 1,
    MaterialState.SHORTAGE: 2,
    MaterialState.UNKNOWN: 3,
}


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


async def lock_orders(session: AsyncSession, order_ids: Iterable[uuid.UUID]) -> list[Order]:
    """Lock orders ``FOR UPDATE`` in ascending id order and return fresh rows."""
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


async def orders_using_materials(
    session: AsyncSession, factory_id: uuid.UUID, material_ids: Collection[uuid.UUID]
) -> list[uuid.UUID]:
    """Ids of the (at most 500, earliest due first) open orders whose BOM uses
    any of ``material_ids``. Takes no locks."""
    if not material_ids:
        return []
    return list((await session.scalars(_orders_using_materials(factory_id, material_ids))).all())


async def lock_orders_using_materials(
    session: AsyncSession,
    factory_id: uuid.UUID,
    material_ids: Collection[uuid.UUID],
    *,
    extra_order_ids: Iterable[uuid.UUID] = (),
) -> list[uuid.UUID]:
    """Lock (ascending id) the open orders whose BOM uses any of
    ``material_ids``, plus ``extra_order_ids``; return the locked ids.

    Call before locking any balance; pass the result to
    `recompute_material_states(order_ids=...)`.
    """
    ids = set(await orders_using_materials(session, factory_id, material_ids))
    ids.update(extra_order_ids)
    return [order.id for order in await lock_orders(session, ids)]


def worst_material_state(states: Iterable[MaterialState]) -> MaterialState:
    """The most severe of ``states`` (`UNKNOWN` when there are none)."""
    return max(states, key=lambda state: _SEVERITY[state], default=MaterialState.UNKNOWN)


def _combine(states: Iterable[MaterialState]) -> MaterialState:
    return worst_material_state(states)


async def recompute_material_states(
    session: AsyncSession,
    factory_id: uuid.UUID,
    material_ids: Collection[uuid.UUID],
    *,
    order_ids: Collection[uuid.UUID] | None = None,
) -> dict[uuid.UUID, MaterialState]:
    """Recompute `orders.material_state` for DRAFT/VALIDATED/PLANNED orders.

    Which orders:

    * ``order_ids`` given: exactly those orders of ``factory_id`` (their row
      locks are (re)taken, which is a no-op when the caller already holds
      them). Callers holding a balance lock must use this form, with ids
      locked *before* the balance (`lock_orders_using_materials`).
    * ``order_ids is None``: the (at most 500, earliest due first) open
      orders whose BOM uses any of ``material_ids``, locked here. Only call
      this form when no slot/balance lock is held in the transaction.

    Per BOM line, with the order's remaining units (`quantity -
    produced_units`): demand = `gross_demand`, dated at
    `min(as_of, due_date)` so an overdue order's demand is never pushed
    past its own due date; the order may use the factory's `available_now`
    plus its own ACTIVE reservations of that material; OPEN expected
    receipts dated on or before the due date count toward the projection at
    the due date. Task 4's `material_state` decides each line; the order
    takes the most severe line state. A missing balance, a missing BOM, or
    an unapproved unit conversion yields `UNKNOWN`. Orders whose state
    changes get `version + 1`.

    Returns the new state of every order that was evaluated.
    """
    factory = await session.get(Factory, factory_id)
    if factory is None:
        return {}
    if order_ids is None:
        candidate_ids: Collection[uuid.UUID] = await orders_using_materials(
            session, factory_id, material_ids
        )
    else:
        candidate_ids = order_ids
    orders = [
        order
        for order in await lock_orders(session, candidate_ids)
        if order.factory_id == factory_id and order.production_state in MATERIAL_STATE_ORDER_STATES
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
        demand_date = min(as_of, order.due_date)
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
                demand=[(demand_date, demand)],
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
