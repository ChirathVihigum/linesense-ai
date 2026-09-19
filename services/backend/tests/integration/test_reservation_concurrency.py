"""Reference fixture 5: concurrent reservations never oversubscribe a
material (task-8-brief.md, docs/architecture/formulas.md).

Each attempt runs in its own session (its own connection and transaction)
on the app-role engine. The first attempt locks the balance row, signals
the second attempt to start, and then holds the lock with
``pg_sleep(0.2)`` so the second attempt is guaranteed to be waiting on the
row lock (not merely racing it) before the first reservation commits.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.auth.policy import Principal
from app.db.models import MaterialBalance, MaterialLot, Order, Reservation, StockMovement
from app.domain.inventory import service as inventory_service
from app.domain.vocab import MaterialLotStatus, ProductionState, ReservationStatus
from tests.factories import make_balance, make_order
from tests.helpers.auth import IdentityFixture, seed_identity
from tests.helpers.inventory import make_material_order, storekeeper_principal

pytestmark = pytest.mark.integration

D = Decimal
REPEATS = 10


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _attempt(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    balance: MaterialBalance,
    order_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    locked: asyncio.Event,
    hold_first: bool,
) -> str:
    async with session_factory() as session:
        try:
            if hold_first:
                await inventory_service.lock_balances(session, [balance.id])
                locked.set()
                await session.execute(text("SELECT pg_sleep(0.2)"))
            else:
                await locked.wait()
            await inventory_service.reserve_material(
                session,
                organization_id=balance.organization_id,
                factory_id=balance.factory_id,
                material_id=balance.material_id,
                order_id=order_id,
                quantity=D(80),
                actor_user_id=actor_user_id,
            )
        except AppError as exc:
            await session.rollback()
            assert exc.status_code == 409
            assert exc.code == "CONFLICT"
            assert exc.message == "Insufficient available material"
            return "conflict"
        await session.commit()
        return "reserved"


@pytest.mark.parametrize("repeat", range(REPEATS))
async def test_two_concurrent_reservations_of_80_from_100(
    repeat: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    balance = await make_balance(
        db_session, organization=identity.organization, factory=ktn, on_hand_accepted=100
    )
    first_order = await make_order(db_session, organization=identity.organization, factory=ktn)
    second_order = await make_order(db_session, organization=identity.organization, factory=ktn)
    actor = identity.users["storekeeper@demo.test"].id
    await db_session.commit()

    locked = asyncio.Event()
    outcomes = await asyncio.gather(
        _attempt(
            session_factory,
            balance=balance,
            order_id=first_order.id,
            actor_user_id=actor,
            locked=locked,
            hold_first=True,
        ),
        _attempt(
            session_factory,
            balance=balance,
            order_id=second_order.id,
            actor_user_id=actor,
            locked=locked,
            hold_first=False,
        ),
    )
    assert outcomes == ["reserved", "conflict"], repeat

    async with session_factory() as check:
        final = await check.get(MaterialBalance, balance.id)
        reservations = (
            await check.scalars(
                select(Reservation).where(
                    Reservation.material_id == balance.material_id,
                    Reservation.status == ReservationStatus.ACTIVE.value,
                )
            )
        ).all()
    assert final is not None
    assert final.reserved == D(80)
    assert final.on_hand_accepted == D(100)
    assert final.version == 2
    assert [r.order_id for r in reservations] == [first_order.id]


async def test_ungated_concurrent_reservations_never_oversubscribe(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    """No coordination at all: whichever transaction locks first wins."""
    ktn = identity.factories["KTN"]
    balance = await make_balance(
        db_session, organization=identity.organization, factory=ktn, on_hand_accepted=100
    )
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()

    async def reserve() -> str:
        async with session_factory() as session:
            try:
                await inventory_service.reserve_material(
                    session,
                    organization_id=identity.organization.id,
                    factory_id=ktn.id,
                    material_id=balance.material_id,
                    order_id=order.id,
                    quantity=D(80),
                    actor_user_id=None,
                )
            except AppError:
                await session.rollback()
                return "conflict"
            await session.commit()
            return "reserved"

    outcomes = await asyncio.gather(reserve(), reserve(), reserve())
    assert sorted(outcomes) == ["conflict", "conflict", "reserved"]
    async with session_factory() as check:
        final = await check.get(MaterialBalance, balance.id)
    assert final is not None and final.reserved == D(80)


# --------------------------------------------------------------------------
# Cancellation-style release racing a storekeeper release or consuming issue
# --------------------------------------------------------------------------


async def _release_setup(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> tuple[Principal, Order, Reservation, MaterialLot]:
    ktn = identity.factories["KTN"]
    material, order = await make_material_order(db_session, identity.organization, ktn)
    await db_session.commit()
    principal = await storekeeper_principal(db_session, identity.organization, ktn)
    async with session_factory() as session:
        movement = await inventory_service.record_receipt(
            session,
            principal,
            ktn.id,
            material_id=material.id,
            lot_code=f"LOT-{uuid.uuid4().hex[:8]}",
            quantity=D(100),
            accept=True,
        )
        reservation = await inventory_service.reserve_material(
            session,
            organization_id=identity.organization.id,
            factory_id=ktn.id,
            material_id=material.id,
            order_id=order.id,
            quantity=D(30),
            actor_user_id=principal.user_id,
        )
        await session.commit()
        lot = await session.get(MaterialLot, movement.lot_id)
    assert lot is not None
    return principal, order, reservation, lot


async def _hold_balance(session: AsyncSession, order: Order, locked: asyncio.Event) -> None:
    """Take the storekeeper-command locks (orders, then balance) and hold them."""
    balance_id = await session.scalar(
        select(MaterialBalance.id).where(MaterialBalance.factory_id == order.factory_id)
    )
    assert balance_id is not None
    await inventory_service.lock_orders_using_materials(
        session, order.factory_id, [], extra_order_ids=[order.id]
    )
    await inventory_service.lock_balances(session, [balance_id])
    locked.set()
    await session.execute(text("SELECT pg_sleep(0.2)"))


async def _cancel_release(
    session_factory: async_sessionmaker[AsyncSession],
    order_id: uuid.UUID,
    *,
    locked: asyncio.Event,
    first: bool,
) -> int:
    """What order cancellation does: release the order's reservations.

    When second, it deliberately skips the order row lock so the balance
    lock and the re-read of ACTIVE reservations are what prevent a double
    release.
    """
    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        if first:
            order.production_state = ProductionState.CANCELLED.value
            order.version += 1
            await session.flush()
            balance_id = await session.scalar(
                select(MaterialBalance.id).where(MaterialBalance.factory_id == order.factory_id)
            )
            assert balance_id is not None
            await inventory_service.lock_balances(session, [balance_id])
            locked.set()
            await session.execute(text("SELECT pg_sleep(0.2)"))
        else:
            await locked.wait()
        released = await inventory_service.release_order_reservations(session, order)
        await session.commit()
        return len(released)


async def _assert_invariants(
    session_factory: async_sessionmaker[AsyncSession], material_id: uuid.UUID
) -> MaterialBalance:
    async with session_factory() as check:
        balance = await check.scalar(
            select(MaterialBalance).where(MaterialBalance.material_id == material_id)
        )
        ledger = await check.scalar(
            select(sa.func.coalesce(sa.func.sum(StockMovement.quantity), 0))
            .join(MaterialLot, MaterialLot.id == StockMovement.lot_id)
            .where(
                StockMovement.material_id == material_id,
                MaterialLot.status == MaterialLotStatus.ACCEPTED.value,
            )
        )
        active = await check.scalar(
            select(sa.func.coalesce(sa.func.sum(Reservation.quantity), 0)).where(
                Reservation.material_id == material_id,
                Reservation.status == ReservationStatus.ACTIVE.value,
            )
        )
    assert balance is not None
    assert balance.on_hand_accepted == ledger
    assert balance.reserved == active
    return balance


@pytest.mark.parametrize("cancel_first", [True, False])
@pytest.mark.parametrize("repeat", range(5))
async def test_cancel_release_vs_storekeeper_release(
    repeat: int,
    cancel_first: bool,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    principal, order, reservation, _ = await _release_setup(db_session, session_factory, identity)
    locked = asyncio.Event()

    async def storekeeper_release() -> str:
        async with session_factory() as session:
            try:
                if not cancel_first:
                    await _hold_balance(session, order, locked)
                else:
                    await locked.wait()
                await inventory_service.release_reservation(session, principal, reservation.id)
            except AppError as exc:
                await session.rollback()
                assert exc.status_code == 409
                return "conflict"
            await session.commit()
            return "released"

    cancelled, storekeeper = await asyncio.gather(
        _cancel_release(session_factory, order.id, locked=locked, first=cancel_first),
        storekeeper_release(),
    )
    if cancel_first:
        assert (cancelled, storekeeper) == (1, "conflict"), repeat
    else:
        assert (cancelled, storekeeper) == (0, "released"), repeat

    balance = await _assert_invariants(session_factory, reservation.material_id)
    assert balance.reserved == D(0)
    assert balance.version == 4  # created, receipt, reserve, exactly one release
    async with session_factory() as check:
        final = await check.get(Reservation, reservation.id)
    assert final is not None and final.status == ReservationStatus.RELEASED.value


@pytest.mark.parametrize("cancel_first", [True, False])
@pytest.mark.parametrize("repeat", range(5))
async def test_cancel_release_vs_consuming_issue(
    repeat: int,
    cancel_first: bool,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    principal, order, reservation, lot = await _release_setup(db_session, session_factory, identity)
    locked = asyncio.Event()

    async def consuming_issue() -> str:
        async with session_factory() as session:
            if not cancel_first:
                await _hold_balance(session, order, locked)
            else:
                await locked.wait()
            await inventory_service.record_issue(
                session,
                principal,
                order.factory_id,
                material_id=reservation.material_id,
                lot_id=lot.id,
                quantity=D(30),
                order_id=order.id,
                reason="cutting",
            )
            await session.commit()
            return "issued"

    cancelled, issued = await asyncio.gather(
        _cancel_release(session_factory, order.id, locked=locked, first=cancel_first),
        consuming_issue(),
    )
    assert issued == "issued"
    balance = await _assert_invariants(session_factory, reservation.material_id)
    assert balance.on_hand_accepted == D(70)
    assert balance.reserved == D(0)
    async with session_factory() as check:
        final = await check.get(Reservation, reservation.id)
    assert final is not None
    if cancel_first:
        assert cancelled == 1, repeat
        assert final.status == ReservationStatus.RELEASED.value
    else:
        assert cancelled == 0, repeat
        assert final.status == ReservationStatus.CONSUMED.value
