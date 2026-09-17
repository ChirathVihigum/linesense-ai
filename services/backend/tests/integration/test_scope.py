"""Organization/factory scoping helpers and shared auth dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_optional_principal, require_idempotency_key
from app.api.errors import AppError
from app.auth.policy import Principal
from app.auth.scope import accessible_factory_ids, load_scoped, visible_factories
from app.auth.sessions import create_session, load_principal, resolve_session, revoke_session
from app.db.models import Factory, Material, Order
from app.main import create_app
from app.settings import Settings
from tests.factories import make_factory, make_material, make_order, make_user
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _principal(session: AsyncSession, identity: IdentityFixture, email: str) -> Principal:
    _, record = await create_session(session, identity.users[email].id)
    principal = await load_principal(session, record)
    assert principal is not None
    return principal


async def test_load_scoped_enforces_org_and_factory(
    db_session: AsyncSession, identity: IdentityFixture
) -> None:
    ktn, byg = identity.factories["KTN"], identity.factories["BYG"]
    org = identity.organization
    ktn_order = await make_order(db_session, organization=org, factory=ktn)
    byg_order = await make_order(db_session, organization=org, factory=byg)
    foreign_order = await make_order(db_session)
    viewer = await _principal(db_session, identity, "viewer@demo.test")
    admin = await _principal(db_session, identity, "admin@demo.test")

    assert (await load_scoped(db_session, Order, ktn_order.id, viewer, "order:read")).id == (
        ktn_order.id
    )

    # Accessible factory, missing permission -> 403.
    with pytest.raises(AppError) as forbidden:
        await load_scoped(db_session, Order, ktn_order.id, viewer, "order:create")
    assert (forbidden.value.status_code, forbidden.value.code) == (403, "FORBIDDEN")

    # No role in the factory, another organization, or no such row -> 404.
    for order_id in (byg_order.id, foreign_order.id, uuid.uuid4()):
        with pytest.raises(AppError) as missing:
            await load_scoped(db_session, Order, order_id, viewer, "order:read")
        assert (missing.value.status_code, missing.value.code) == (404, "NOT_FOUND")

    # Org-wide admin reads both plants but not other organizations.
    await load_scoped(db_session, Order, byg_order.id, admin, "order:read")
    with pytest.raises(AppError) as foreign:
        await load_scoped(db_session, Order, foreign_order.id, admin, "order:read")
    assert foreign.value.status_code == 404

    # Factories themselves and org-level rows are scoped too.
    await load_scoped(db_session, Factory, ktn.id, viewer, "order:read")
    with pytest.raises(AppError):
        await load_scoped(db_session, Factory, byg.id, viewer, "order:read")
    material = await make_material(db_session, organization=org)
    foreign_material = await make_material(db_session)
    await load_scoped(db_session, Material, material.id, viewer, "inventory:read")
    with pytest.raises(AppError) as material_forbidden:
        await load_scoped(db_session, Material, material.id, viewer, "inventory:write")
    assert material_forbidden.value.status_code == 403
    with pytest.raises(AppError) as material_missing:
        await load_scoped(db_session, Material, foreign_material.id, viewer, "inventory:read")
    assert material_missing.value.status_code == 404


async def test_accessible_and_visible_factories(
    db_session: AsyncSession, identity: IdentityFixture
) -> None:
    ktn, byg = identity.factories["KTN"], identity.factories["BYG"]
    await make_factory(db_session)  # another organization's factory never appears
    planner = await _principal(db_session, identity, "planner@demo.test")
    admin = await _principal(db_session, identity, "admin@demo.test")

    assert await accessible_factory_ids(db_session, planner, "order:create") == [ktn.id]
    assert await accessible_factory_ids(db_session, planner, "order:dispatch") == []
    assert await accessible_factory_ids(db_session, admin, "admin:manage") == [byg.id, ktn.id]
    assert await accessible_factory_ids(db_session, admin, "order:create") == []
    assert [f.id for f in await visible_factories(db_session, planner)] == [ktn.id]
    assert [f.id for f in await visible_factories(db_session, admin)] == [byg.id, ktn.id]


async def test_session_lifecycle(db_session: AsyncSession) -> None:
    user_id = (await make_user(db_session)).id
    raw, record = await create_session(db_session, user_id, max_age_seconds=60)
    assert raw != record.token_hash
    assert await resolve_session(db_session, raw) is not None
    assert await resolve_session(db_session, raw + "x") is None
    assert await resolve_session(db_session, None) is None

    # A user without membership has no principal.
    assert await load_principal(db_session, record) is None

    await revoke_session(db_session, record.id)
    await db_session.flush()
    db_session.expire_all()
    assert await resolve_session(db_session, raw) is None

    expired_raw, _ = await create_session(db_session, user_id, max_age_seconds=0)
    assert await resolve_session(db_session, expired_raw) is None


_router = APIRouter(prefix="/api/v1/_scope_test")


@_router.get("/optional")
async def _optional(
    principal: Principal | None = Depends(get_optional_principal),
) -> dict[str, str]:
    return {"user": str(principal.user_id) if principal else "anonymous"}


@_router.get("/idempotent")
async def _idempotent(key: str = Depends(require_idempotency_key)) -> dict[str, str]:
    return {"key": key}


@pytest_asyncio.fixture
async def dep_client(settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    app: FastAPI = create_app(settings)
    app.include_router(_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def test_optional_principal_and_idempotency_header(
    dep_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await dep_client.get("/api/v1/_scope_test/optional")).json() == {"user": "anonymous"}

    outsider = await login_as(dep_client, session_factory, "outsider@example.test")
    assert outsider.me is None
    assert (await dep_client.get("/api/v1/_scope_test/optional")).json() == {"user": "anonymous"}

    planner = await login_as(dep_client, session_factory, "planner@demo.test")
    assert (await dep_client.get("/api/v1/_scope_test/optional")).json() == {
        "user": str(planner.user.id)
    }

    missing = await dep_client.get("/api/v1/_scope_test/idempotent")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    for bad in ("short", "k" * 129):
        response = await dep_client.get(
            "/api/v1/_scope_test/idempotent", headers={"Idempotency-Key": bad}
        )
        assert response.status_code == 422
    ok = await dep_client.get(
        "/api/v1/_scope_test/idempotent", headers={"Idempotency-Key": "k" * 8}
    )
    assert ok.json() == {"key": "k" * 8}
