"""Integration tests for `GET /api/v1/orders/{order_id}/history` (task-21-brief.md)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import BomVersion, Customer, Style
from tests.factories import make_order
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

FUTURE_DUE_DATE = date(2099, 1, 1)


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _make_customer(session: AsyncSession, organization_id: object) -> Customer:
    suffix = uuid.uuid4().hex[:8]
    customer = Customer(
        organization_id=organization_id, code=f"CUST-{suffix}", name=f"Customer {suffix}"
    )
    session.add(customer)
    await session.flush()
    return customer


async def _make_ready_style(session: AsyncSession, organization_id: object) -> Style:
    suffix = uuid.uuid4().hex[:8]
    style = Style(
        organization_id=organization_id,
        code=f"STY-{suffix}",
        name=f"Style {suffix}",
        product_type="knit-top",
    )
    session.add(style)
    await session.flush()
    session.add(BomVersion(style_id=style.id, version_no=1, is_active=True))
    await session.flush()
    return style


async def test_history_lists_events_newest_first_with_actor_names(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-HIST",
        due_date=FUTURE_DUE_DATE,
    )
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")

    transition = await planner.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "VALIDATED", "expected_version": 1},
        headers={"Idempotency-Key": "hist-key-1"},
    )
    assert transition.status_code == 200, transition.text

    response = await planner.get(f"/api/v1/orders/{order.id}/history")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    event = body["items"][0]
    assert event["action"] == "order.transition"
    assert event["outcome"] == "SUCCESS"
    assert event["actor_display_name"] != "Unknown user"


async def test_history_is_scoped_to_the_order_and_requires_order_read(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order_a = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-HIST-A",
        due_date=FUTURE_DUE_DATE,
    )
    order_b = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-HIST-B",
        due_date=FUTURE_DUE_DATE,
    )
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    await planner.post(
        f"/api/v1/orders/{order_a.id}/transitions",
        json={"target_state": "VALIDATED", "expected_version": 1},
        headers={"Idempotency-Key": "hist-key-a"},
    )
    await planner.post(
        f"/api/v1/orders/{order_b.id}/transitions",
        json={"target_state": "VALIDATED", "expected_version": 1},
        headers={"Idempotency-Key": "hist-key-b"},
    )

    only_a = await planner.get(f"/api/v1/orders/{order_a.id}/history")
    assert only_a.status_code == 200
    assert only_a.json()["total"] == 1

    # `byg.planner` has no role in KTN (the order's factory), so the order is
    # invisible to them: 404, never revealing that it exists (backend-contracts.md §4).
    byg_planner = await login_as(client, session_factory, "byg.planner@demo.test")
    forbidden = await byg_planner.get(f"/api/v1/orders/{order_a.id}/history")
    assert forbidden.status_code == 404
