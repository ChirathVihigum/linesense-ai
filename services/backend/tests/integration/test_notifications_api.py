"""Integration tests for `app.api.notifications` (task-7-brief.md fix round 1:
missing coverage for notifications list + mark-read)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.domain.clock as clock_module
from app.db.models import Notification
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def test_list_includes_own_and_role_targeted_notifications(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner_user = identity.users["planner@demo.test"]

    personal = Notification(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        user_id=planner_user.id,
        role=None,
        kind="test",
        title="Personal note",
        body="For the planner only",
    )
    role_targeted = Notification(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        user_id=None,
        role="supervisor",
        kind="test",
        title="Supervisor broadcast",
        body="For every supervisor",
    )
    db_session.add_all([personal, role_targeted])
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    planner_list = await planner.get(f"/api/v1/factories/{ktn.id}/notifications")
    assert planner_list.status_code == 200
    planner_titles = {item["title"] for item in planner_list.json()["items"]}
    assert planner_titles == {"Personal note"}

    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    supervisor_list = await supervisor.get(f"/api/v1/factories/{ktn.id}/notifications")
    assert supervisor_list.status_code == 200
    supervisor_titles = {item["title"] for item in supervisor_list.json()["items"]}
    assert supervisor_titles == {"Supervisor broadcast"}


async def test_mark_read_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner_user = identity.users["planner@demo.test"]
    notification = Notification(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        user_id=planner_user.id,
        role=None,
        kind="test",
        title="Read me",
        body="body",
    )
    db_session.add(notification)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")

    first = await planner.post(f"/api/v1/notifications/{notification.id}/read")
    assert first.status_code == 200
    first_read_at = first.json()["read_at"]
    assert first_read_at is not None

    second = await planner.post(f"/api/v1/notifications/{notification.id}/read")
    assert second.status_code == 200
    # The same instant, not necessarily the same string: a value round-tripped
    # through Postgres may come back in the server's offset rather than "Z".
    assert datetime.fromisoformat(second.json()["read_at"]) == datetime.fromisoformat(first_read_at)


async def test_mark_read_uses_the_patched_clock(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `app.api.notifications` imports the `clock` module (not the `utcnow`
    # name), so patching `clock_module.utcnow` here reaches it -- proving
    # `read_at` is not bound to the real name imported at module load time.
    frozen = datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)
    monkeypatch.setattr(clock_module, "utcnow", lambda: frozen)

    ktn = identity.factories["KTN"]
    planner_user = identity.users["planner@demo.test"]
    notification = Notification(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        user_id=planner_user.id,
        role=None,
        kind="test",
        title="Frozen clock",
        body="body",
    )
    db_session.add(notification)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.post(f"/api/v1/notifications/{notification.id}/read")
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["read_at"]) == frozen


async def test_mark_read_for_someone_elses_notification_is_404(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner_user = identity.users["planner@demo.test"]
    notification = Notification(
        organization_id=identity.organization.id,
        factory_id=ktn.id,
        user_id=planner_user.id,
        role=None,
        kind="test",
        title="Not yours",
        body="body",
    )
    db_session.add(notification)
    await db_session.commit()

    viewer = await login_as(client, session_factory, "viewer@demo.test")
    response = await viewer.post(f"/api/v1/notifications/{notification.id}/read")
    assert response.status_code == 404
