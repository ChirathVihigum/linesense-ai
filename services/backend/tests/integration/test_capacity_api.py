"""Integration tests for the capacity service and routes (task-8-brief.md)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import AppError
from app.db.models import Allocation, AuditEvent, LineCapability, LineCapacitySlot, Order
from app.domain.capacity import service as capacity_service
from tests.factories import make_line, make_order, make_slot, make_style_with_operations
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

D = Decimal
SLOT_DATE = date(2026, 12, 1)


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def test_capacity_board_reports_remaining_and_utilization(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=ktn, code="L01")
    db_session.add(LineCapability(line_id=line.id, skill_code="SEW"))
    # capacity = 4800 * 0.75 = 3600
    slot = await make_slot(db_session, line=line, slot_date=SLOT_DATE, shift_code="A")
    await make_slot(db_session, line=line, slot_date=SLOT_DATE, shift_code="B")
    await make_slot(db_session, line=line, slot_date=date(2026, 12, 20), shift_code="A")
    order = await make_order(
        db_session, organization=identity.organization, factory=ktn, external_ref="PO-CAP1"
    )
    actor = identity.users["planner@demo.test"].id
    await db_session.commit()

    async with session_factory() as session:
        await capacity_service.lock_slots(session, [slot.id])
        allocation = await capacity_service.allocate(
            session,
            slot_id=slot.id,
            order_id=order.id,
            standard_minutes=D(900),
            units=D(600),
            actor_user_id=actor,
            recommendation_id=None,
        )
        await session.commit()

    async with session_factory() as check:
        audit = await check.scalar(
            select(AuditEvent).where(AuditEvent.action == "capacity.allocate")
        )
    assert audit is not None and audit.target_id == str(allocation.id)

    viewer = await login_as(client, session_factory, "viewer@demo.test")
    response = await viewer.get(
        f"/api/v1/factories/{ktn.id}/capacity",
        params={"start": "2026-12-01", "end": "2026-12-05"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start"] == "2026-12-01" and body["end"] == "2026-12-05"
    assert len(body["lines"]) == 1
    board_line = body["lines"][0]
    assert board_line["code"] == "L01"
    assert [s["shift_code"] for s in board_line["slots"]] == ["A", "B"]
    first = board_line["slots"][0]
    assert D(first["capacity_standard_minutes"]) == D(3600)
    assert D(first["allocated_standard_minutes"]) == D(900)
    assert D(first["remaining_standard_minutes"]) == D(2700)
    assert D(first["utilization"]) == D("0.25")
    assert first["version"] == 2
    assert first["allocations"] == [
        {
            "id": str(allocation.id),
            "order_id": str(order.id),
            "order_external_ref": "PO-CAP1",
            "standard_minutes": first["allocations"][0]["standard_minutes"],
            "units": first["allocations"][0]["units"],
        }
    ]
    assert D(first["allocations"][0]["standard_minutes"]) == D(900)
    second = board_line["slots"][1]
    assert D(second["utilization"]) == D(0)
    assert second["allocations"] == []

    lines = await viewer.get(f"/api/v1/factories/{ktn.id}/lines")
    assert lines.status_code == 200
    assert lines.json()["total"] == 1
    assert lines.json()["items"][0]["skill_codes"] == ["SEW"]

    too_long = await viewer.get(
        f"/api/v1/factories/{ktn.id}/capacity",
        params={"start": "2026-12-01", "end": "2027-01-02"},
    )
    assert too_long.status_code == 422
    reversed_range = await viewer.get(
        f"/api/v1/factories/{ktn.id}/capacity",
        params={"start": "2026-12-05", "end": "2026-12-01"},
    )
    assert reversed_range.status_code == 422


async def test_capacity_routes_are_factory_scoped(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    await make_line(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()
    byg_planner = await login_as(client, session_factory, "byg.planner@demo.test")
    lines = await byg_planner.get(f"/api/v1/factories/{ktn.id}/lines")
    board = await byg_planner.get(
        f"/api/v1/factories/{ktn.id}/capacity",
        params={"start": "2026-12-01", "end": "2026-12-02"},
    )
    assert lines.status_code == 404
    assert board.status_code == 404


async def test_allocate_beyond_remaining_conflicts(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=ktn)
    slot = await make_slot(
        db_session,
        line=line,
        available_operator_minutes=200,
        planned_efficiency=D("0.5"),
        allocated_standard_minutes=40,
    )
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()

    async with session_factory() as session:
        await capacity_service.lock_slots(session, [slot.id])
        with pytest.raises(AppError) as excinfo:
            await capacity_service.allocate(
                session,
                slot_id=slot.id,
                order_id=order.id,
                standard_minutes=D("60.01"),
                units=D(10),
                actor_user_id=None,
                recommendation_id=None,
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "CONFLICT"
        # Exactly the remaining 60 minutes still fits.
        await capacity_service.allocate(
            session,
            slot_id=slot.id,
            order_id=order.id,
            standard_minutes=D(60),
            units=D(10),
            actor_user_id=None,
            recommendation_id=None,
        )
        await session.commit()

    async with session_factory() as check:
        refreshed = await check.get(LineCapacitySlot, slot.id)
    assert refreshed is not None
    assert refreshed.allocated_standard_minutes == D(100)
    assert refreshed.version == 2


async def test_compatible_lines_and_slot_capacities(
    db_session: AsyncSession, identity: IdentityFixture
) -> None:
    ktn = identity.factories["KTN"]
    byg = identity.factories["BYG"]
    style = await make_style_with_operations(db_session, organization=identity.organization)
    sew = await make_line(db_session, organization=identity.organization, factory=ktn)
    db_session.add(LineCapability(line_id=sew.id, skill_code="SEW"))
    other = await make_line(db_session, organization=identity.organization, factory=ktn)
    db_session.add(LineCapability(line_id=other.id, skill_code="PRESS"))
    inactive = await make_line(
        db_session, organization=identity.organization, factory=ktn, is_active=False
    )
    db_session.add(LineCapability(line_id=inactive.id, skill_code="SEW"))
    foreign = await make_line(db_session, organization=identity.organization, factory=byg)
    db_session.add(LineCapability(line_id=foreign.id, skill_code="SEW"))
    slot = await make_slot(db_session, line=sew, slot_date=SLOT_DATE)
    await make_slot(db_session, line=other, slot_date=SLOT_DATE)
    await make_slot(db_session, line=sew, slot_date=date(2026, 12, 3))
    await db_session.flush()

    compatible = await capacity_service.compatible_line_ids(db_session, ktn.id, style.id)
    assert compatible == frozenset({sew.id})

    capacities = await capacity_service.slot_capacities(
        db_session, ktn.id, start=SLOT_DATE, end=date(2026, 12, 2), line_ids=[sew.id]
    )
    assert [c.slot_id for c in capacities] == [slot.id]
    assert capacities[0].capacity_standard_minutes == D(3600)
    all_lines = await capacity_service.slot_capacities(
        db_session, ktn.id, start=SLOT_DATE, end=date(2026, 12, 3)
    )
    assert len(all_lines) == 3
    await db_session.rollback()


async def _allocate_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    slot_id: uuid.UUID,
    order_id: uuid.UUID,
    locked: asyncio.Event,
    hold_first: bool,
) -> str:
    async with session_factory() as session:
        try:
            if hold_first:
                await capacity_service.lock_slots(session, [slot_id])
                locked.set()
                await session.execute(text("SELECT pg_sleep(0.2)"))
            else:
                await locked.wait()
                await capacity_service.lock_slots(session, [slot_id])
            await capacity_service.allocate(
                session,
                slot_id=slot_id,
                order_id=order_id,
                standard_minutes=D(60),
                units=D(20),
                actor_user_id=None,
                recommendation_id=None,
            )
        except AppError as exc:
            await session.rollback()
            assert exc.status_code == 409
            return "conflict"
        await session.commit()
        return "allocated"


@pytest.mark.parametrize("repeat", range(5))
async def test_concurrent_allocations_never_exceed_capacity(
    repeat: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=ktn)
    slot = await make_slot(
        db_session, line=line, available_operator_minutes=100, planned_efficiency=1
    )
    first = await make_order(db_session, organization=identity.organization, factory=ktn)
    second = await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()

    locked = asyncio.Event()
    outcomes: list[Any] = await asyncio.gather(
        _allocate_attempt(
            session_factory, slot_id=slot.id, order_id=first.id, locked=locked, hold_first=True
        ),
        _allocate_attempt(
            session_factory, slot_id=slot.id, order_id=second.id, locked=locked, hold_first=False
        ),
    )
    assert outcomes == ["allocated", "conflict"], repeat

    async with session_factory() as check:
        refreshed = await check.get(LineCapacitySlot, slot.id)
        allocations = (
            await check.scalars(select(Allocation).where(Allocation.slot_id == slot.id))
        ).all()
    assert refreshed is not None
    assert refreshed.allocated_standard_minutes == D(60)
    assert [a.order_id for a in allocations] == [first.id]


async def test_capacity_routes_require_authentication(app: Any) -> None:
    """The capacity router is mounted and rejects anonymous callers."""
    paths = set(app.openapi()["paths"])
    assert "/api/v1/factories/{factory_id}/capacity" in paths
    assert "/api/v1/factories/{factory_id}/lines" in paths
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        response = await c.get("/api/v1/factories/00000000-0000-0000-0000-000000000000/lines")
    assert response.status_code == 401


async def test_style_without_operations_has_no_compatible_line(
    db_session: AsyncSession, identity: IdentityFixture
) -> None:
    ktn = identity.factories["KTN"]
    style = await make_style_with_operations(
        db_session, organization=identity.organization, operation_count=0
    )
    line = await make_line(db_session, organization=identity.organization, factory=ktn)
    db_session.add(LineCapability(line_id=line.id, skill_code="SEW"))
    await db_session.flush()
    assert await capacity_service.compatible_line_ids(db_session, ktn.id, style.id) == frozenset()
    await db_session.rollback()


@pytest.mark.parametrize("repeat", range(5))
async def test_concurrent_order_allocation_releases_apply_once(
    repeat: int,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=ktn)
    slot = await make_slot(
        db_session, line=line, available_operator_minutes=100, planned_efficiency=1
    )
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()
    async with session_factory() as session:
        await capacity_service.lock_slots(session, [slot.id])
        await capacity_service.allocate(
            session,
            slot_id=slot.id,
            order_id=order.id,
            standard_minutes=D(60),
            units=D(20),
            actor_user_id=None,
            recommendation_id=None,
        )
        await session.commit()

    locked = asyncio.Event()

    async def release(first: bool) -> int:
        async with session_factory() as session:
            refreshed = await session.get(Order, order.id)
            assert refreshed is not None
            if first:
                await capacity_service.lock_slots(session, [slot.id])
                locked.set()
                await session.execute(text("SELECT pg_sleep(0.2)"))
            else:
                await locked.wait()
            released = await capacity_service.release_order_allocations(session, refreshed)
            await session.commit()
            return len(released)

    outcomes = await asyncio.gather(release(True), release(False))
    assert outcomes == [1, 0], repeat
    async with session_factory() as check:
        final = await check.get(LineCapacitySlot, slot.id)
    assert final is not None
    assert final.allocated_standard_minutes == D(0)
    assert final.version == 3  # created, allocated, released exactly once
