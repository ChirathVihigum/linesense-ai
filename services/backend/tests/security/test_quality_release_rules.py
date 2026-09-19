"""Security tests for quality-release separation of duties (task-9-brief.md).

Verifies: an order's version must match at release time (409 STALE_INPUT), a
release must reference an inspection made under the *currently* active
policy version (409 CONFLICT), and the releaser must differ from the
inspector of the inspection being released (403 SELF_APPROVAL_DENIED).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.vocab import PolicyStatus, ProductionState
from tests.factories import make_order, make_quality_policy
from tests.helpers.auth import AuthedClient, IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

RULES = {
    "sample_size": 5,
    "max_defective_units": 2,
    "max_critical_defects": 0,
    "required_inspection_types": ["FINAL"],
}


def _key(name: str) -> dict[str, str]:
    return {"Idempotency-Key": f"release-rules-{name}-{uuid.uuid4().hex[:8]}"}


def _inspection_payload() -> dict[str, Any]:
    return {"inspection_type": "FINAL", "inspected_units": 10, "defective_units": 0, "defects": []}


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


async def _order_with_pass(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    identity: IdentityFixture,
) -> tuple[Any, Any, AuthedClient, str, int]:
    factory = identity.factories["KTN"]
    policy = await make_quality_policy(
        db_session, organization=identity.organization, code="QP-DEMO", rules=RULES
    )
    order = await make_order(
        db_session,
        organization=identity.organization,
        factory=factory,
        production_state=ProductionState.PRODUCTION_COMPLETE.value,
        packed_units=100,
        quantity=100,
    )
    await db_session.commit()
    quality_user = await login_as(client, session_factory, "quality@demo.test")
    inspect = await quality_user.post(
        f"/api/v1/orders/{order.id}/inspections", json=_inspection_payload(), headers=_key("setup")
    )
    assert inspect.status_code == 201, inspect.text
    body = inspect.json()
    return order, policy, quality_user, body["inspections"][0]["id"], body["order_version"]


async def test_release_with_stale_order_version_is_409(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    identity: IdentityFixture,
) -> None:
    order, _policy, quality_user, inspection_id, order_version = await _order_with_pass(
        client, session_factory, db_session, identity
    )
    response = await quality_user.post(
        f"/api/v1/orders/{order.id}/quality-release",
        json={
            "inspection_id": inspection_id,
            "expected_order_version": order_version + 1,
            "notes": None,
        },
        headers=_key("stale"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_INPUT"


async def test_release_by_the_inspector_is_self_approval_denied(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    identity: IdentityFixture,
) -> None:
    order, _policy, quality_user, inspection_id, order_version = await _order_with_pass(
        client, session_factory, db_session, identity
    )
    response = await quality_user.post(
        f"/api/v1/orders/{order.id}/quality-release",
        json={
            "inspection_id": inspection_id,
            "expected_order_version": order_version,
            "notes": None,
        },
        headers=_key("self"),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SELF_APPROVAL_DENIED"


async def test_release_against_a_superseded_policy_version_is_conflict(
    client: AsyncClient,
    app: Any,
    session_factory: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
    identity: IdentityFixture,
) -> None:
    order, policy, _quality_user, inspection_id, order_version = await _order_with_pass(
        client, session_factory, db_session, identity
    )
    # A new policy version supersedes the one the inspection was evaluated under.
    policy.status = PolicyStatus.RETIRED.value
    await make_quality_policy(
        db_session,
        organization=identity.organization,
        code="QP-DEMO",
        version_no=2,
        rules=RULES,
    )
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as other_client:
        other_quality_user = await login_as(other_client, session_factory, "quality.b@demo.test")
        response = await other_quality_user.post(
            f"/api/v1/orders/{order.id}/quality-release",
            json={
                "inspection_id": inspection_id,
                "expected_order_version": order_version,
                "notes": None,
            },
            headers=_key("superseded"),
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
