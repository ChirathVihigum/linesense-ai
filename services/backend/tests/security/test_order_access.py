"""Cross-tenant/cross-factory access tests for the orders API (task-7-brief.md).

Note on the "list /factories/{KTN}/orders -> 403" bullet in the brief: per
backend-contracts.md section 4 ("a row in ... a factory where the principal
holds no role at all raises 404 NOT_FOUND"), and `app/auth/scope.py`'s
`load_scoped`, a factory the caller holds *no* role in at all (as `byg.planner`
holds none at KTN) is indistinguishable from a nonexistent one and always
yields 404, never 403 - regardless of whether the resource is the factory
itself (list route) or a row scoped to it (detail/transition routes). 403 is
reserved for an *accessible* factory where the caller lacks the specific
permission. This test asserts the documented, implemented behavior (404) for
all three of `byg.planner`'s KTN accesses, consistently.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories import make_order
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def test_byg_planner_cannot_see_or_change_ktn_orders(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    order = await make_order(db_session, organization=identity.organization, factory=ktn)
    await db_session.commit()

    byg_planner = await login_as(client, session_factory, "byg.planner@demo.test")

    detail = await byg_planner.get(f"/api/v1/orders/{order.id}")
    assert detail.status_code == 404

    transition = await byg_planner.post(
        f"/api/v1/orders/{order.id}/transitions",
        json={"target_state": "VALIDATED", "expected_version": 1},
        headers={"Idempotency-Key": "security-key-0001"},
    )
    assert transition.status_code == 404

    listing = await byg_planner.get(f"/api/v1/factories/{ktn.id}/orders")
    assert listing.status_code == 404


async def test_unauthenticated_request_is_401(
    client: AsyncClient, identity: IdentityFixture
) -> None:
    ktn = identity.factories["KTN"]
    response = await client.get(f"/api/v1/factories/{ktn.id}/orders")
    assert response.status_code == 401


async def test_unknown_order_id_is_404(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    planner = await login_as(client, session_factory, "planner@demo.test")
    response = await planner.get(f"/api/v1/orders/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_missing_csrf_token_is_403(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    ktn = identity.factories["KTN"]
    authed = await login_as(client, session_factory, "planner@demo.test")
    response = await client.post(
        f"/api/v1/factories/{ktn.id}/orders",
        json={
            "external_ref": "PO-CSRF",
            "customer_id": str(uuid.uuid4()),
            "style_id": str(uuid.uuid4()),
            "quantity": 1,
            "due_date": "2099-01-01",
            "priority": 3,
        },
        headers={"Idempotency-Key": "security-key-0002", "Origin": authed.origin},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
