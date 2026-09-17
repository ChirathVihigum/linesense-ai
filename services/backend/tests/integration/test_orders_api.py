"""Integration tests for `app.api.orders` (task-7-brief.md)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Allocation,
    AuditEvent,
    BomVersion,
    Customer,
    LineCapacitySlot,
    MaterialBalance,
    Notification,
    Order,
    Reservation,
    Style,
)
from app.domain.vocab import AllocationStatus, ProductionState, ReservationStatus
from tests.factories import make_balance, make_line, make_material, make_order, make_slot
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

FUTURE_DUE_DATE = date(2099, 1, 1)


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _make_ready_style(session: AsyncSession, organization_id: uuid.UUID) -> Style:
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


async def _make_customer(session: AsyncSession, organization_id: uuid.UUID) -> Customer:
    suffix = uuid.uuid4().hex[:8]
    customer = Customer(
        organization_id=organization_id, code=f"CUST-{suffix}", name=f"Customer {suffix}"
    )
    session.add(customer)
    await session.flush()
    return customer


async def _setup_order_inputs(
    db_session: AsyncSession, identity: IdentityFixture
) -> tuple[Customer, Style]:
    customer = await _make_customer(db_session, identity.organization.id)
    style = await _make_ready_style(db_session, identity.organization.id)
    await db_session.commit()
    return customer, style


async def test_create_replay_and_duplicate(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    planner = await login_as(client, session_factory, "planner@demo.test")

    payload: dict[str, Any] = {
        "external_ref": "PO-1001",
        "customer_id": str(customer.id),
        "style_id": str(style.id),
        "quantity": 500,
        "due_date": FUTURE_DUE_DATE.isoformat(),
        "priority": 2,
    }
    headers = {"Idempotency-Key": "create-key-0001"}

    first = await planner.post(f"/api/v1/factories/{ktn.id}/orders", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["external_ref"] == "PO-1001"
    assert body["production_state"] == "DRAFT"
    assert body["material_state"] == "UNKNOWN"
    assert body["quality_state"] == "NOT_INSPECTED"
    assert body["version"] == 1

    replay = await planner.post(f"/api/v1/factories/{ktn.id}/orders", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == body

    async with session_factory() as check:
        count = await check.scalar(select(Order).where(Order.external_ref == "PO-1001"))
        assert count is not None
        rows = (await check.scalars(select(Order.id).where(Order.external_ref == "PO-1001"))).all()
        assert len(rows) == 1

    duplicate = await planner.post(
        f"/api/v1/factories/{ktn.id}/orders",
        json=payload,
        headers={"Idempotency-Key": "create-key-0002"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"


async def test_viewer_create_is_denied_and_audited(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    viewer = await login_as(client, session_factory, "viewer@demo.test")

    payload = {
        "external_ref": "PO-DENY1",
        "customer_id": str(customer.id),
        "style_id": str(style.id),
        "quantity": 10,
        "due_date": FUTURE_DUE_DATE.isoformat(),
        "priority": 3,
    }
    response = await viewer.post(
        f"/api/v1/factories/{ktn.id}/orders",
        json=payload,
        headers={"Idempotency-Key": "deny-key-0001"},
    )
    assert response.status_code == 403

    async with session_factory() as check:
        event = await check.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "order.create", AuditEvent.outcome == "DENIED"
            )
        )
    assert event is not None
    assert event.factory_id == ktn.id
    assert event.actor_id == str(viewer.user.id)


async def test_list_filters_and_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    for ref, state, due in (
        ("PO-A", ProductionState.DRAFT.value, date(2099, 1, 1)),
        ("PO-B", ProductionState.VALIDATED.value, date(2099, 1, 2)),
        ("PO-C", ProductionState.DRAFT.value, date(2099, 1, 3)),
    ):
        await make_order(
            db_session,
            organization=identity.organization,
            factory=ktn,
            customer=customer,
            style=style,
            bom_version=bom_version,
            external_ref=ref,
            due_date=due,
            production_state=state,
        )
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")

    all_orders = await planner.get(f"/api/v1/factories/{ktn.id}/orders")
    assert all_orders.status_code == 200
    assert all_orders.json()["total"] == 3

    draft_only = await planner.get(
        f"/api/v1/factories/{ktn.id}/orders", params={"production_state": "DRAFT"}
    )
    assert draft_only.status_code == 200
    refs = [item["external_ref"] for item in draft_only.json()["items"]]
    assert refs == ["PO-A", "PO-C"]

    paged = await planner.get(
        f"/api/v1/factories/{ktn.id}/orders", params={"limit": 1, "offset": 1}
    )
    assert paged.status_code == 200
    assert paged.json()["total"] == 3
    assert [item["external_ref"] for item in paged.json()["items"]] == ["PO-B"]

    by_q = await planner.get(f"/api/v1/factories/{ktn.id}/orders", params={"q": "PO-B"})
    assert [item["external_ref"] for item in by_q.json()["items"]] == ["PO-B"]

    by_customer = await planner.get(
        f"/api/v1/factories/{ktn.id}/orders", params={"q": customer.code}
    )
    assert by_customer.json()["total"] == 3


async def test_detail_allowed_transitions_depend_on_caller(
    client: AsyncClient,
    app: Any,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-TRANS",
        due_date=FUTURE_DUE_DATE,
    )
    await db_session.commit()

    # Two identities are needed concurrently: `login_as` mutates the shared
    # cookie jar of whatever client it is given, so each identity needs its
    # own `AsyncClient` (see tests/helpers/auth.py's `login_as` docstring).
    planner = await login_as(client, session_factory, "planner@demo.test")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as second_client:
        supervisor = await login_as(second_client, session_factory, "supervisor@demo.test")

        planner_detail = await planner.get(f"/api/v1/orders/{order.id}")
        assert planner_detail.status_code == 200
        assert planner_detail.json()["allowed_transitions"] == ["VALIDATED"]

        supervisor_detail = await supervisor.get(f"/api/v1/orders/{order.id}")
        assert supervisor_detail.json()["allowed_transitions"] == ["CANCELLED", "VALIDATED"]
        assert supervisor_detail.json()["bom"]["version_no"] == bom_version.version_no


async def test_validate_transition_and_stale_version(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-VALIDATE",
        due_date=FUTURE_DUE_DATE,
    )
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")

    stale = await planner.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "VALIDATED", "expected_version": 99},
        headers={"Idempotency-Key": "trans-key-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_INPUT"

    ok = await planner.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "VALIDATED", "expected_version": 1},
        headers={"Idempotency-Key": "trans-key-ok"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["production_state"] == "VALIDATED"
    assert ok.json()["version"] == 2

    async with session_factory() as check:
        notification = await check.scalar(
            select(Notification).where(
                Notification.factory_id == ktn.id, Notification.role == "supervisor"
            )
        )
    assert notification is not None
    assert notification.user_id is None


async def test_planned_via_endpoint_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-PLANNED",
        due_date=FUTURE_DUE_DATE,
        production_state=ProductionState.VALIDATED.value,
    )
    await db_session.commit()

    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    response = await supervisor.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "PLANNED", "expected_version": 1},
        headers={"Idempotency-Key": "trans-key-planned"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "INVALID_TRANSITION"
    assert "recommendation" in body["error"]["message"]


async def test_dispatch_ineligible_order_reports_reasons(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-DISPATCH",
        due_date=FUTURE_DUE_DATE,
        production_state=ProductionState.PRODUCTION_COMPLETE.value,
        produced_units=100,
        packed_units=0,
        quantity=100,
    )
    await db_session.commit()

    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    response = await supervisor.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "DISPATCHED", "expected_version": 1},
        headers={"Idempotency-Key": "trans-key-dispatch"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "CONFLICT"
    reasons = {fe["message"] for fe in body["error"]["field_errors"]}
    assert "POLICY_UNKNOWN" in reasons
    assert "PACKING_INCOMPLETE" in reasons
    assert "NO_QUALITY_RELEASE" in reasons


async def test_cancel_releases_allocations_and_reservations(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-CANCEL",
        due_date=FUTURE_DUE_DATE,
        production_state=ProductionState.PLANNED.value,
    )
    line = await make_line(db_session, organization=identity.organization, factory=ktn)
    slot = await make_slot(
        db_session,
        line=line,
        available_operator_minutes=1000,
        planned_efficiency=1,
        allocated_standard_minutes=200,
    )
    allocation = Allocation(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        order_id=order.id,
        slot_id=slot.id,
        standard_minutes=200,
        units=100,
        status=AllocationStatus.ACTIVE.value,
    )
    db_session.add(allocation)

    material = await make_material(db_session, organization=identity.organization)
    balance = await make_balance(
        db_session,
        organization=identity.organization,
        factory=ktn,
        material=material,
        on_hand_accepted=500,
        reserved=150,
    )
    reservation = Reservation(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        material_id=material.id,
        order_id=order.id,
        quantity=150,
        status=ReservationStatus.ACTIVE.value,
    )
    db_session.add(reservation)
    await db_session.commit()

    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    response = await supervisor.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "CANCELLED", "expected_version": 1},
        headers={"Idempotency-Key": "trans-key-cancel"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["production_state"] == "CANCELLED"

    async with session_factory() as check:
        refreshed_allocation = await check.get(Allocation, allocation.id)
        refreshed_slot = await check.get(LineCapacitySlot, slot.id)
        refreshed_reservation = await check.get(Reservation, reservation.id)
        refreshed_balance = await check.get(MaterialBalance, balance.id)

    assert refreshed_allocation is not None and refreshed_allocation.status == "RELEASED"
    assert refreshed_slot is not None and refreshed_slot.allocated_standard_minutes == 0
    assert refreshed_slot.version == 2
    assert refreshed_reservation is not None and refreshed_reservation.status == "RELEASED"
    assert refreshed_balance is not None and refreshed_balance.reserved == 0
    assert refreshed_balance.version == 2


async def test_progress_cannot_decrease(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    customer, style = await _setup_order_inputs(db_session, identity)
    ktn = identity.factories["KTN"]
    bom_version = await db_session.scalar(select(BomVersion).where(BomVersion.style_id == style.id))
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=ktn,
        customer=customer,
        style=style,
        bom_version=bom_version,
        external_ref="PO-PROGRESS",
        due_date=FUTURE_DUE_DATE,
        production_state=ProductionState.IN_PRODUCTION.value,
        quantity=100,
    )
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")

    forward = await planner.post(
        f"/api/v1/orders/{order.id}/progress",
        json={"produced_units": 40, "packed_units": 10, "expected_version": 1},
        headers={"Idempotency-Key": "progress-key-1"},
    )
    assert forward.status_code == 200, forward.text
    assert forward.json()["produced_units"] == 40
    assert forward.json()["version"] == 2

    backward = await planner.post(
        f"/api/v1/orders/{order.id}/progress",
        json={"produced_units": 10, "packed_units": 10, "expected_version": 2},
        headers={"Idempotency-Key": "progress-key-2"},
    )
    assert backward.status_code == 409
    assert backward.json()["error"]["code"] == "CONFLICT"

    over = await planner.post(
        f"/api/v1/orders/{order.id}/progress",
        json={"produced_units": 500, "packed_units": 10, "expected_version": 2},
        headers={"Idempotency-Key": "progress-key-3"},
    )
    assert over.status_code == 409
