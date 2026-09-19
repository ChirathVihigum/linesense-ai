"""Integration tests for the IE API: observations, outlier marking,
operator aliases, and line/style bottleneck analysis (task-9-brief.md)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Factory, Line, Style, StyleOperation
from app.domain.clock import utcnow
from app.seed import scenario as demo_scenario
from app.seed.generator import seed_demo
from tests.factories import (
    make_cycle_observation,
    make_line,
    make_operation_staffing,
    make_operator_alias,
    make_style_with_operations,
)
from tests.helpers.auth import IdentityFixture, login_as, seed_identity

pytestmark = pytest.mark.integration

D = Decimal


def _key(name: str) -> dict[str, str]:
    return {"Idempotency-Key": f"ie-{name}-{uuid.uuid4().hex[:8]}"}


@pytest.fixture
async def identity(db_session: AsyncSession) -> IdentityFixture:
    fixture = await seed_identity(db_session)
    await db_session.commit()
    return fixture


@pytest.fixture
async def rig(db_session: AsyncSession, identity: IdentityFixture):
    """A minimal line/style/operation/alias for a single-operation analysis."""
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    style = await make_style_with_operations(
        db_session, organization=identity.organization, operation_count=1
    )
    operation = (
        await db_session.scalars(select(StyleOperation).where(StyleOperation.style_id == style.id))
    ).one()
    await make_operation_staffing(db_session, line, operation, parallel_operators=1)
    alias = await make_operator_alias(
        db_session, organization=identity.organization, factory=factory
    )
    await db_session.commit()
    return factory, line, style, operation, alias


def _observation_payload(
    line: Line, style_id: uuid.UUID, operation: StyleOperation, alias_code: str
) -> dict:
    return {
        "line_id": str(line.id),
        "style_id": str(style_id),
        "operation_id": str(operation.id),
        "operator_alias_code": alias_code,
        "observed_seconds": "40",
        "observed_at": utcnow().isoformat(),
    }


async def test_seeded_demo_line_reproduces_op04_bottleneck(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    anchor_date = date(2026, 1, 5)
    await seed_demo(db_session, anchor_date=anchor_date, issuer="https://idp.ie-api-test.example")
    await db_session.commit()
    # The seed dates its observations from `anchor_date - 29` through
    # `anchor_date`, not relative to wall-clock "now"; ask for a window wide
    # enough to still cover that fixed range however much (test) time has
    # passed since it.
    window_days = max(30, (date.today() - anchor_date).days + 35)

    factory = await db_session.scalar(select(Factory).where(Factory.code == "KTN"))
    assert factory is not None
    line = await db_session.scalar(
        select(Line).where(
            Line.factory_id == factory.id, Line.code == demo_scenario.DEMO_IE_LINE_CODE
        )
    )
    assert line is not None
    style = await db_session.scalar(
        select(Style).where(Style.code == demo_scenario.DEMO_STYLE_CODE)
    )
    assert style is not None

    ie_user = await login_as(client, session_factory, "ie@demo.test")
    response = await ie_user.get(
        f"/api/v1/factories/{factory.id}/ie/lines/{line.id}/styles/{style.id}/analysis",
        params={"window_days": window_days},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["balance"] is not None
    bottleneck_operation = body["operations"][body["balance"]["bottleneck_index"]]
    assert bottleneck_operation["code"] == demo_scenario.DEMO_IE_BOTTLENECK_OPERATION_CODE
    bottleneck_seconds = D(body["balance"]["bottleneck_effective_seconds"])
    assert (
        demo_scenario.DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_LOW
        <= bottleneck_seconds
        <= demo_scenario.DEMO_IE_EXPECTED_BOTTLENECK_SECONDS_HIGH
    )


async def test_outlier_exclusion_changes_representative_value(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    rig,
) -> None:
    factory, line, style, operation, alias = rig
    now = utcnow()
    for seconds in (D("39"), D("40"), D("41")):
        await make_cycle_observation(
            db_session, line, operation, alias, observed_seconds=seconds, observed_at=now
        )
    outlier = await make_cycle_observation(
        db_session, line, operation, alias, observed_seconds=D("500"), observed_at=now
    )
    await db_session.commit()

    ie_user = await login_as(client, session_factory, "ie@demo.test")
    url = f"/api/v1/factories/{factory.id}/ie/lines/{line.id}/styles/{style.id}/analysis"

    before = await ie_user.get(url)
    assert before.status_code == 200, before.text
    before_representative = D(before.json()["operations"][0]["representative_seconds"])
    assert before_representative == D("40.5")  # median of 39, 40, 41, 500

    mark = await ie_user.post(
        f"/api/v1/ie/observations/{outlier.id}/outlier",
        json={"reason": "measurement error"},
        headers=_key("outlier"),
    )
    assert mark.status_code == 200, mark.text
    assert mark.json()["is_outlier"] is True

    after = await ie_user.get(url)
    after_representative = D(after.json()["operations"][0]["representative_seconds"])
    assert after_representative == D("40")
    assert after_representative != before_representative


async def test_insufficient_samples_flagged_and_balance_none(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    identity: IdentityFixture,
) -> None:
    factory = identity.factories["KTN"]
    line = await make_line(db_session, organization=identity.organization, factory=factory)
    style = await make_style_with_operations(
        db_session, organization=identity.organization, operation_count=2
    )
    operations = list(
        (
            await db_session.scalars(
                select(StyleOperation)
                .where(StyleOperation.style_id == style.id)
                .order_by(StyleOperation.sequence)
            )
        ).all()
    )
    alias = await make_operator_alias(
        db_session, organization=identity.organization, factory=factory
    )
    for operation in operations:
        await make_operation_staffing(db_session, line, operation)

    now = utcnow()
    for seconds in (D("30"), D("31"), D("29")):
        await make_cycle_observation(
            db_session, line, operations[0], alias, observed_seconds=seconds, observed_at=now
        )
    for seconds in (D("20"), D("21")):  # only 2 samples: below min_samples=3
        await make_cycle_observation(
            db_session, line, operations[1], alias, observed_seconds=seconds, observed_at=now
        )
    await db_session.commit()

    ie_user = await login_as(client, session_factory, "ie@demo.test")
    response = await ie_user.get(
        f"/api/v1/factories/{factory.id}/ie/lines/{line.id}/styles/{style.id}/analysis"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["balance"] is None
    by_code = {op["code"]: op for op in body["operations"]}
    assert by_code[operations[0].code]["insufficient_samples"] is False
    assert by_code[operations[1].code]["insufficient_samples"] is True
    assert by_code[operations[1].code]["representative_seconds"] is None
    assert any("fewer than 3" in limitation for limitation in body["limitations"])


async def test_only_ie_engineer_can_record_observation(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    rig,
) -> None:
    factory, line, style, operation, alias = rig
    viewer = await login_as(client, session_factory, "viewer@demo.test")
    response = await viewer.post(
        f"/api/v1/factories/{factory.id}/ie/observations",
        json=_observation_payload(line, style.id, operation, alias.alias_code),
        headers=_key("viewer"),
    )
    assert response.status_code == 403


async def test_future_observed_at_is_rejected(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    rig,
) -> None:
    factory, line, style, operation, alias = rig
    ie_user = await login_as(client, session_factory, "ie@demo.test")
    payload = _observation_payload(line, style.id, operation, alias.alias_code)
    payload["observed_at"] = (utcnow() + timedelta(days=1)).isoformat()
    response = await ie_user.post(
        f"/api/v1/factories/{factory.id}/ie/observations",
        json=payload,
        headers=_key("future"),
    )
    assert response.status_code == 422


async def test_unknown_alias_is_rejected(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    rig,
) -> None:
    factory, line, style, operation, alias = rig
    ie_user = await login_as(client, session_factory, "ie@demo.test")
    payload = _observation_payload(line, style.id, operation, alias.alias_code)
    payload["operator_alias_code"] = "UNKNOWN-ALIAS"
    response = await ie_user.post(
        f"/api/v1/factories/{factory.id}/ie/observations",
        json=payload,
        headers=_key("unknown-alias"),
    )
    assert response.status_code == 422


async def test_record_observation_success_and_operator_aliases_listing(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    rig,
) -> None:
    factory, line, style, operation, alias = rig
    ie_user = await login_as(client, session_factory, "ie@demo.test")
    response = await ie_user.post(
        f"/api/v1/factories/{factory.id}/ie/observations",
        json=_observation_payload(line, style.id, operation, alias.alias_code),
        headers=_key("record"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["operator_alias_id"] == str(alias.id)
    assert body["is_outlier"] is False

    aliases = await ie_user.get(f"/api/v1/factories/{factory.id}/ie/operator-aliases")
    assert aliases.status_code == 200
    codes = {item["alias_code"] for item in aliases.json()["items"]}
    assert alias.alias_code in codes
