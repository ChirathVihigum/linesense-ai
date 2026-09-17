"""Integration tests for `app.api.audit` (task-7-brief.md)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_audit
from app.domain.vocab import ActorType, AuditOutcome
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def test_supervisor_sees_events_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    org = identity.organization
    for index, action in enumerate(["order.create", "order.transition", "order.progress"]):
        await record_audit(
            db_session,
            organization_id=org.id,
            factory_id=ktn.id,
            actor_type=ActorType.USER.value,
            actor_id="tester",
            action=action,
            target_type="order",
            target_id=str(index),
            outcome=AuditOutcome.SUCCESS.value,
        )
    await db_session.commit()

    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    response = await supervisor.get(f"/api/v1/factories/{ktn.id}/audit-events")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    actions = [item["action"] for item in body["items"]]
    assert actions == ["order.progress", "order.transition", "order.create"]

    filtered = await supervisor.get(
        f"/api/v1/factories/{ktn.id}/audit-events", params={"action": "order.create"}
    )
    assert [item["action"] for item in filtered.json()["items"]] == ["order.create"]


async def test_planner_is_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/factories/{ktn.id}/audit-events")
    assert response.status_code == 403


async def test_org_admin_sees_org_level_events_too(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    org = identity.organization
    await record_audit(
        db_session,
        organization_id=org.id,
        factory_id=None,
        actor_type=ActorType.USER.value,
        actor_id="tester",
        action="settings.update",
        target_type="settings",
        target_id="1",
        outcome=AuditOutcome.SUCCESS.value,
    )
    await record_audit(
        db_session,
        organization_id=org.id,
        factory_id=ktn.id,
        actor_type=ActorType.USER.value,
        actor_id="tester",
        action="order.create",
        target_type="order",
        target_id="1",
        outcome=AuditOutcome.SUCCESS.value,
    )
    await db_session.commit()

    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.get(f"/api/v1/factories/{ktn.id}/audit-events")
    assert response.status_code == 200
    actions = {item["action"] for item in response.json()["items"]}
    assert actions == {"settings.update", "order.create"}

    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    supervisor_view = await supervisor.get(f"/api/v1/factories/{ktn.id}/audit-events")
    supervisor_actions = {item["action"] for item in supervisor_view.json()["items"]}
    assert supervisor_actions == {"order.create"}
