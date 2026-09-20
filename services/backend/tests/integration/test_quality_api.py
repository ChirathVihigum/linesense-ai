"""Integration tests for the quality API: inspections, holds, separated
releases, shipment eligibility, and policies (task-9-brief.md)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent
from app.domain.vocab import ProductionState
from tests.factories import make_order, make_quality_policy
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


def _key(name: str) -> dict[str, str]:
    return {"Idempotency-Key": f"quality-{name}-{uuid.uuid4().hex[:8]}"}


def _inspection_payload(
    *,
    inspected_units: int = 10,
    defective_units: int = 0,
    defects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "inspection_type": "FINAL",
        "inspected_units": inspected_units,
        "defective_units": defective_units,
        "defects": defects or [],
    }


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


@pytest.fixture
async def order_with_policy(db_session: AsyncSession, identity: IdentityFixture):
    factory = identity.factories["KTN"]
    policy = await make_quality_policy(
        db_session,
        organization=identity.organization,
        code="QP-DEMO",
        rules={
            "sample_size": 5,
            "max_defective_units": 2,
            "max_critical_defects": 0,
            "required_inspection_types": ["FINAL"],
        },
    )
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=factory,
        quantity=100,
        production_state=ProductionState.PRODUCTION_COMPLETE.value,
        packed_units=100,
    )
    await db_session.commit()
    return order, policy


async def test_no_inspections_means_not_inspected_and_shipment_ineligible(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    order_with_policy: Any,
) -> None:
    order, _policy = order_with_policy
    quality_user = await login_as(client, session_factory, "quality@demo.test")
    response = await quality_user.get(f"/api/v1/orders/{order.id}/quality")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quality_state"] == "NOT_INSPECTED"
    assert body["shipment"]["eligible"] is False
    assert "INSPECTION_MISSING:FINAL" in body["shipment"]["reasons"]
    assert "NO_QUALITY_RELEASE" in body["shipment"]["reasons"]


async def test_no_active_policy_returns_409_on_inspect(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    identity: IdentityFixture,
) -> None:
    order = await make_order(
        db_session, organization=identity.organization, factory=identity.factories["KTN"]
    )
    await db_session.commit()
    quality_user = await login_as(client, session_factory, "quality@demo.test")
    response = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(),
        headers=_key("no-policy"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_viewer_cannot_inspect(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], order_with_policy: Any
) -> None:
    order, _policy = order_with_policy
    viewer = await login_as(client, session_factory, "viewer@demo.test")
    response = await viewer.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(),
        headers=_key("viewer"),
    )
    assert response.status_code == 403


async def test_failed_inspection_creates_hold_and_release_of_it_is_conflict(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], order_with_policy: Any
) -> None:
    order, _policy = order_with_policy
    quality_user = await login_as(client, session_factory, "quality@demo.test")

    response = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(
            defective_units=5, defects=[{"defect_code": "STITCH", "severity": "MAJOR", "count": 5}]
        ),
        headers=_key("fail"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["inspections"][0]["result"] == "FAIL"
    assert body["quality_state"] == "HOLD"
    assert len(body["holds"]) == 1
    assert body["holds"][0]["status"] == "ACTIVE"
    assert body["holds"][0]["reason"].startswith("Automatic hold:")

    release = await quality_user.post(
        f"/api/v1/orders/{order.id}/quality-release",
        json={
            "inspection_id": body["inspections"][0]["id"],
            "expected_order_version": body["order_version"],
            "notes": None,
        },
        headers=_key("release-failed"),
    )
    assert release.status_code == 409


async def test_failed_inspection_auto_hold_writes_its_own_audit_event(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    order_with_policy: Any,
) -> None:
    order, _policy = order_with_policy
    quality_user = await login_as(client, session_factory, "quality@demo.test")

    response = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(
            defective_units=5, defects=[{"defect_code": "STITCH", "severity": "MAJOR", "count": 5}]
        ),
        headers=_key("fail-audit"),
    )
    assert response.status_code == 201, response.text
    hold_id = response.json()["holds"][0]["id"]

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "quality.hold.auto_create")
    )
    assert event is not None
    assert event.target_type == "quality_hold"
    assert event.target_id == hold_id
    assert event.actor_type == "SYSTEM"
    assert event.outcome == "SUCCESS"
    assert event.reason is not None and event.reason.startswith("Automatic hold:")

    # The inspection's own audit event still exists too: this is an
    # additional event, not a replacement.
    inspection_event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "quality.inspection.record")
    )
    assert inspection_event is not None


async def test_pass_then_release_by_different_user_releases_holds_and_order(
    client: AsyncClient,
    app: Any,
    session_factory: async_sessionmaker[AsyncSession],
    order_with_policy: Any,
) -> None:
    order, _policy = order_with_policy
    quality_user = await login_as(client, session_factory, "quality@demo.test")

    inspect = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(),
        headers=_key("pass"),
    )
    assert inspect.status_code == 201, inspect.text
    body = inspect.json()
    assert body["quality_state"] == "PENDING"
    inspection_id = body["inspections"][0]["id"]
    order_version = body["order_version"]

    self_release = await quality_user.post(
        f"/api/v1/orders/{order.id}/quality-release",
        json={
            "inspection_id": inspection_id,
            "expected_order_version": order_version,
            "notes": None,
        },
        headers=_key("self-release"),
    )
    assert self_release.status_code == 403
    assert self_release.json()["error"]["code"] == "SELF_APPROVAL_DENIED"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as other_client:
        other_quality_user = await login_as(other_client, session_factory, "quality.b@demo.test")
        release = await other_quality_user.post(
            f"/api/v1/orders/{order.id}/quality-release",
            json={
                "inspection_id": inspection_id,
                "expected_order_version": order_version,
                "notes": "ok",
            },
            headers=_key("release"),
        )
        assert release.status_code == 200, release.text
        released_body = release.json()

    assert released_body["quality_state"] == "RELEASED"
    assert released_body["shipment"]["eligible"] is True
    assert released_body["shipment"]["reasons"] == []
    assert len(released_body["releases"]) == 1

    # A later FINAL inspection (any result) invalidates the release for shipment.
    later = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(defective_units=1),
        headers=_key("later"),
    )
    assert later.status_code == 201, later.text
    later_body = later.json()
    assert later_body["shipment"]["eligible"] is False
    assert "NO_QUALITY_RELEASE" in later_body["shipment"]["reasons"]


async def test_list_factory_holds_and_policies(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], order_with_policy: Any
) -> None:
    order, _policy = order_with_policy
    quality_user = await login_as(client, session_factory, "quality@demo.test")
    fail = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections",
        json=_inspection_payload(
            defective_units=5, defects=[{"defect_code": "STITCH", "severity": "MAJOR", "count": 5}]
        ),
        headers=_key("fail-list"),
    )
    assert fail.status_code == 201, fail.text

    holds = await quality_user.get(
        f"/api/v1/factories/{order.factory_id}/quality/holds", params={"status": "ACTIVE"}
    )
    assert holds.status_code == 200
    assert any(h["order_id"] == str(order.id) for h in holds.json()["items"])

    policies = await quality_user.get(f"/api/v1/factories/{order.factory_id}/quality/policies")
    assert policies.status_code == 200
    codes = {p["code"] for p in policies.json()["items"]}
    assert "QP-DEMO" in codes

    trends = await quality_user.get(f"/api/v1/factories/{order.factory_id}/quality/trends")
    assert trends.status_code == 200
    assert trends.json()["inspected_units"] >= 10
