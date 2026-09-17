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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.db.models import MaterialBalance, Reservation
from app.domain.inventory import service as inventory_service
from app.domain.vocab import ReservationStatus
from tests.factories import make_balance, make_order
from tests.helpers.auth import IdentityFixture, seed_identity

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
