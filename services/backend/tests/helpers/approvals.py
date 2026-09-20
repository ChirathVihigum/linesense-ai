"""Shared setup for the approval integration/security tests (task-14-brief.md).

`propose` runs the real Task 13 flow (analysis request -> worker -> agents ->
recommendation) so the tests decide and apply a genuinely agent-produced
proposal rather than a hand-built one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Factory, Order, Recommendation
from app.domain.vocab import RecommendationStatus
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from app.settings import Settings
from tests.helpers.auth import AuthedClient, login_as
from tests.helpers.worker import drain


@dataclass
class Proposal:
    order_id: uuid.UUID
    factory_id: uuid.UUID
    run_id: uuid.UUID
    recommendation_id: uuid.UUID
    proposal_hash: str
    proposer: AuthedClient


async def seed(db_session: AsyncSession, settings: Settings) -> tuple[uuid.UUID, uuid.UUID, int]:
    """Seed the demo scenario; return ``(order_id, factory_id, order_version)``."""
    anchor = datetime.now(ZoneInfo("Asia/Colombo")).date()
    await seed_demo(db_session, anchor_date=anchor, issuer=settings.oidc_issuer)
    order = await db_session.scalar(sa.select(Order).where(Order.external_ref == DEMO_ORDER_REF))
    assert order is not None
    factory = await db_session.get(Factory, order.factory_id)
    assert factory is not None and factory.code == "KTN"
    result = (order.id, factory.id, order.version)
    await db_session.commit()
    return result


async def propose(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
    *,
    requested_by: str = "planner@demo.test",
) -> Proposal:
    """Seed the demo scenario and run one analysis as ``requested_by``."""
    order_id, factory_id, order_version = await seed(db_session, settings)
    proposer = await login_as(client, session_factory, requested_by)
    accepted = await proposer.post(
        f"/api/v1/orders/{order_id}/analyses",
        json={"expected_order_version": order_version},
        headers={"Idempotency-Key": f"propose-{uuid.uuid4().hex}"},
    )
    assert accepted.status_code == 202, accepted.text
    run_id = uuid.UUID(accepted.json()["run_id"])
    await drain(
        session_factory, settings, transport=ASGITransport(app=app, raise_app_exceptions=False)
    )
    async with session_factory() as session:
        recommendation = await session.scalar(
            sa.select(Recommendation).where(
                Recommendation.run_id == run_id,
                Recommendation.status == RecommendationStatus.PROPOSED.value,
            )
        )
        assert recommendation is not None, "the demo flow must produce a PROPOSED recommendation"
        return Proposal(
            order_id=order_id,
            factory_id=factory_id,
            run_id=run_id,
            recommendation_id=recommendation.id,
            proposal_hash=recommendation.proposal_hash,
            proposer=proposer,
        )


def key(prefix: str) -> dict[str, str]:
    return {"Idempotency-Key": f"{prefix}-{uuid.uuid4().hex}"}
