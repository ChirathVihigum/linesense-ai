"""Two approved proposals racing for the last minutes of one capacity slot.

Both recommendations are built directly (with *valid* input versions) so the
race is about the apply transaction alone. The first attempt takes the same
locks `approvals.apply` takes, in the same order (order row, then slot),
signals the second attempt, and holds the locks with ``pg_sleep`` so the
second attempt is provably *waiting on the row lock* rather than merely
racing it.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.db.models import Allocation, Factory, LineCapacitySlot, Order, Recommendation
from app.domain.approvals import service as approvals
from app.domain.capacity.service import lock_slots
from app.domain.vocab import (
    AllocationStatus,
    ProductionState,
    RecommendationStatus,
    Role,
)
from tests.factories import make_line, make_order, make_recommendation, make_run, make_slot
from tests.helpers.auth import IdentityFixture, seed_identity
from tests.helpers.inventory import principal_for

pytestmark = pytest.mark.integration

D = Decimal
REPEATS = 5
# 4800 operator minutes x 0.75 efficiency = 3600 standard minutes of capacity;
# 3500 are already taken, so exactly 100 remain for two 100-minute proposals.
AVAILABLE_OPERATOR_MINUTES = D(4800)
PLANNED_EFFICIENCY = D("0.75")
ALREADY_ALLOCATED = D(3500)
CAPACITY = AVAILABLE_OPERATOR_MINUTES * PLANNED_EFFICIENCY
CONTESTED_MINUTES = D(100)


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _build(
    session: AsyncSession, identity: IdentityFixture, factory: Factory, slot: LineCapacitySlot
) -> tuple[Order, Recommendation]:
    order = await make_order(
        session,
        organization=identity.organization,
        factory=factory,
        production_state=ProductionState.VALIDATED.value,
    )
    run = await make_run(session, order=order, requested_by=identity.users["planner@demo.test"])
    proposal = {
        "order_id": str(order.id),
        "allocations": [
            {
                "slot_id": str(slot.id),
                "line_id": str(slot.line_id),
                "line_code": "L1",
                "slot_date": slot.slot_date.isoformat(),
                "shift_code": slot.shift_code,
                "standard_minutes": str(CONTESTED_MINUTES),
                "units": "10",
            }
        ],
        "reservations": [],
    }
    recommendation = await make_recommendation(
        session,
        run=run,
        proposer=identity.users["planner@demo.test"],
        status=RecommendationStatus.APPROVED.value,
        proposal=proposal,
        proposal_hash=f"hash-{uuid.uuid4().hex}",
        input_versions={
            "order": {str(order.id): order.version},
            "capacity_slots": {str(slot.id): slot.version},
            "material_balances": {},
        },
    )
    return order, recommendation


async def _apply(
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
    factory: Factory,
    *,
    order_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    proposal_hash: str,
    slot_id: uuid.UUID,
    locked: asyncio.Event,
    hold_first: bool,
) -> str:
    principal = principal_for(
        identity.users["supervisor@demo.test"].id,
        identity.organization.id,
        {factory.id: frozenset({Role.SUPERVISOR})},
    )
    async with session_factory() as session:
        try:
            if hold_first:
                # Exactly the apply lock order: order row, then the slot.
                await session.execute(
                    sa.select(Order).where(Order.id == order_id).with_for_update()
                )
                await lock_slots(session, [slot_id])
                locked.set()
                await session.execute(text("SELECT pg_sleep(0.2)"))
            else:
                await locked.wait()
            await approvals.apply(
                session, principal, recommendation_id, proposal_hash=proposal_hash
            )
        except approvals.PersistedRejection as exc:
            await session.commit()
            assert exc.status_code == 409
            assert exc.code == "STALE_INPUT"
            return "stale"
        except AppError as exc:
            await session.rollback()
            assert exc.status_code == 409, f"{exc.code}: {exc.message}"
            assert exc.code == "CONFLICT"
            return "conflict"
        await session.commit()
        return "applied"


@pytest.mark.parametrize("repeat", range(REPEATS))
async def test_two_applications_contend_for_the_last_slot_minutes(
    repeat: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    slot = await make_slot(
        db_session,
        line=line,
        available_operator_minutes=AVAILABLE_OPERATOR_MINUTES,
        planned_efficiency=PLANNED_EFFICIENCY,
        allocated_standard_minutes=ALREADY_ALLOCATED,
    )
    first_order, first_rec = await _build(db_session, identity, factory, slot)
    second_order, second_rec = await _build(db_session, identity, factory, slot)
    await db_session.commit()

    locked = asyncio.Event()
    outcomes = await asyncio.gather(
        _apply(
            session_factory,
            identity,
            factory,
            order_id=first_order.id,
            recommendation_id=first_rec.id,
            proposal_hash=first_rec.proposal_hash,
            slot_id=slot.id,
            locked=locked,
            hold_first=True,
        ),
        _apply(
            session_factory,
            identity,
            factory,
            order_id=second_order.id,
            recommendation_id=second_rec.id,
            proposal_hash=second_rec.proposal_hash,
            slot_id=slot.id,
            locked=locked,
            hold_first=False,
        ),
    )
    assert outcomes[0] == "applied", repeat
    assert outcomes[1] in ("stale", "conflict"), repeat

    async with session_factory() as check:
        final_slot = await check.get(LineCapacitySlot, slot.id)
        assert final_slot is not None
        assert final_slot.allocated_standard_minutes == ALREADY_ALLOCATED + CONTESTED_MINUTES
        assert final_slot.allocated_standard_minutes <= CAPACITY

        allocations = (
            await check.scalars(
                sa.select(Allocation).where(
                    Allocation.slot_id == slot.id,
                    Allocation.status == AllocationStatus.ACTIVE.value,
                )
            )
        ).all()
        assert [row.order_id for row in allocations] == [first_order.id], repeat

        applied = await check.get(Recommendation, first_rec.id)
        loser = await check.get(Recommendation, second_rec.id)
        assert applied is not None and loser is not None
        assert applied.status == RecommendationStatus.APPLIED.value
        assert loser.status in (
            RecommendationStatus.SUPERSEDED.value,
            RecommendationStatus.APPROVED.value,
        )

        winner_order = await check.get(Order, first_order.id)
        loser_order = await check.get(Order, second_order.id)
        assert winner_order is not None and loser_order is not None
        assert winner_order.production_state == ProductionState.PLANNED.value
        assert loser_order.production_state == ProductionState.VALIDATED.value

        assert (
            await check.scalar(
                sa.select(sa.func.count()).select_from(
                    sa.select(Allocation).where(Allocation.order_id == second_order.id).subquery()
                )
            )
            == 0
        ), repeat


async def test_ungated_concurrent_applications_never_exceed_capacity(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    """No coordination at all: whichever transaction locks the slot first wins."""
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    slot = await make_slot(
        db_session,
        line=line,
        available_operator_minutes=AVAILABLE_OPERATOR_MINUTES,
        planned_efficiency=PLANNED_EFFICIENCY,
        allocated_standard_minutes=ALREADY_ALLOCATED,
    )
    built = [await _build(db_session, identity, factory, slot) for _ in range(3)]
    await db_session.commit()

    locked = asyncio.Event()
    locked.set()
    outcomes = await asyncio.gather(
        *(
            _apply(
                session_factory,
                identity,
                factory,
                order_id=order.id,
                recommendation_id=recommendation.id,
                proposal_hash=recommendation.proposal_hash,
                slot_id=slot.id,
                locked=locked,
                hold_first=False,
            )
            for order, recommendation in built
        )
    )
    assert outcomes.count("applied") == 1, outcomes

    async with session_factory() as check:
        final_slot = await check.get(LineCapacitySlot, slot.id)
        assert final_slot is not None
        assert final_slot.allocated_standard_minutes == ALREADY_ALLOCATED + CONTESTED_MINUTES
