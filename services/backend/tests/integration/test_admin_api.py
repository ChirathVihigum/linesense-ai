"""Integration tests for `app.api.admin` (task-22-brief.md requirement 2)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.sessions import SESSION_COOKIE
from app.db.models import AuditEvent, RoleAssignment
from app.llm import FixtureLLMClient
from tests.factories import make_quality_policy
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _role_assignment_id(
    db_session: AsyncSession, membership_id: uuid.UUID, role: str, factory_id: uuid.UUID | None
) -> uuid.UUID | None:
    return await db_session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.membership_id == membership_id,
            RoleAssignment.role == role,
            RoleAssignment.factory_id.is_(None)
            if factory_id is None
            else RoleAssignment.factory_id == factory_id,
        )
    )


async def _me_status_for_token(app: object, token: str) -> int:
    """The `/api/v1/me` status a raw session cookie gets, using a fresh
    client (the shared `client` fixture's cookie jar holds only one
    identity at a time)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as fresh:
        fresh.cookies.set(SESSION_COOKIE, token)
        response = await fresh.get("/api/v1/me")
    return response.status_code


async def test_org_admin_grants_role_and_revokes_target_session(
    client: AsyncClient,
    app: object,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner_membership = identity.memberships["planner@demo.test"]

    await login_as(client, session_factory, "planner@demo.test")
    target_token = client.cookies.get(SESSION_COOKIE)
    assert target_token is not None
    assert await _me_status_for_token(app, target_token) == 200

    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.post(
        f"/api/v1/admin/memberships/{planner_membership.id}/roles",
        json={"role": "storekeeper", "factory_id": str(ktn.id)},
        headers={"Idempotency-Key": "grant-storekeeper-1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert any(
        row["role"] == "storekeeper" and row["factory_id"] == str(ktn.id) for row in body["roles"]
    )

    audit = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "admin.role_grant")
    )
    assert audit is not None and audit.outcome == "SUCCESS"

    assert await _me_status_for_token(app, target_token) == 401


async def test_granting_the_same_role_twice_is_a_no_op(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner_membership = identity.memberships["planner@demo.test"]
    admin = await login_as(client, session_factory, "admin@demo.test")

    first = await admin.post(
        f"/api/v1/admin/memberships/{planner_membership.id}/roles",
        json={"role": "storekeeper", "factory_id": str(ktn.id)},
        headers={"Idempotency-Key": "grant-storekeeper-a"},
    )
    assert first.status_code == 201

    second = await admin.post(
        f"/api/v1/admin/memberships/{planner_membership.id}/roles",
        json={"role": "storekeeper", "factory_id": str(ktn.id)},
        headers={"Idempotency-Key": "grant-storekeeper-b"},
    )
    assert second.status_code == 201
    storekeeper_rows = [row for row in second.json()["roles"] if row["role"] == "storekeeper"]
    assert len(storekeeper_rows) == 1


async def test_supervisor_cannot_grant_roles(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    planner_membership = identity.memberships["planner@demo.test"]
    supervisor = await login_as(client, session_factory, "supervisor@demo.test")
    response = await supervisor.post(
        f"/api/v1/admin/memberships/{planner_membership.id}/roles",
        json={"role": "storekeeper", "factory_id": None},
        headers={"Idempotency-Key": "grant-denied"},
    )
    assert response.status_code == 403

    audit = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "admin.role_grant")
    )
    assert audit is not None and audit.outcome == "DENIED"


async def test_cannot_remove_the_last_org_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    admin_membership = identity.memberships["admin@demo.test"]
    assignment_id = await _role_assignment_id(db_session, admin_membership.id, "org_admin", None)
    assert assignment_id is not None

    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.delete(
        f"/api/v1/admin/role-assignments/{assignment_id}",
        headers={"Idempotency-Key": "revoke-last-admin"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"

    still_there = await _role_assignment_id(db_session, admin_membership.id, "org_admin", None)
    assert still_there == assignment_id


async def test_revoking_a_role_assignment_revokes_the_target_session(
    client: AsyncClient,
    app: object,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    planner_membership = identity.memberships["planner@demo.test"]
    assignment_id = await _role_assignment_id(db_session, planner_membership.id, "planner", ktn.id)
    assert assignment_id is not None

    await login_as(client, session_factory, "planner@demo.test")
    target_token = client.cookies.get(SESSION_COOKIE)
    assert target_token is not None

    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.delete(
        f"/api/v1/admin/role-assignments/{assignment_id}",
        headers={"Idempotency-Key": "revoke-planner-role"},
    )
    assert response.status_code == 200, response.text
    assert all(row["id"] != str(assignment_id) for row in response.json()["roles"])

    assert await _me_status_for_token(app, target_token) == 401


async def test_cannot_deactivate_self(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    admin_membership = identity.memberships["admin@demo.test"]
    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.post(
        f"/api/v1/admin/memberships/{admin_membership.id}/deactivate",
        headers={"Idempotency-Key": "self-deactivate"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_deactivating_another_membership_revokes_their_session(
    client: AsyncClient,
    app: object,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    planner_membership = identity.memberships["planner@demo.test"]
    await login_as(client, session_factory, "planner@demo.test")
    target_token = client.cookies.get(SESSION_COOKIE)
    assert target_token is not None

    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.post(
        f"/api/v1/admin/memberships/{planner_membership.id}/deactivate",
        headers={"Idempotency-Key": "deactivate-planner"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    assert await _me_status_for_token(app, target_token) == 401


async def test_list_memberships_requires_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    viewer = await login_as(client, session_factory, "viewer@demo.test")
    denied = await viewer.get("/api/v1/admin/memberships")
    assert denied.status_code == 403

    admin = await login_as(client, session_factory, "admin@demo.test")
    allowed = await admin.get("/api/v1/admin/memberships")
    assert allowed.status_code == 200
    emails = {row["email"] for row in allowed.json()["items"]}
    assert "planner@demo.test" in emails


async def test_settings_report_read_only_effective_limits(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.get("/api/v1/admin/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_provider"] == "fixture"
    assert body["llm_model"] == FixtureLLMClient.model
    assert body["model_calls_limit"] == 12
    assert body["token_budget"] == 200_000
    assert body["max_tool_calls"] == 4


async def test_list_policies(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    await make_quality_policy(db_session, organization=identity.organization, code="QP-DEMO")
    await db_session.commit()

    admin = await login_as(client, session_factory, "admin@demo.test")
    response = await admin.get("/api/v1/admin/policies")
    assert response.status_code == 200
    codes = {row["code"] for row in response.json()["items"]}
    assert "QP-DEMO" in codes
