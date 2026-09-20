"""Separation of duties on recommendations (task-14-brief.md).

The proposer may never decide or apply their own proposal; only supervisors
of the recommendation's own factory may decide at all.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent, Recommendation
from app.domain.vocab import AuditOutcome, RecommendationStatus
from app.settings import Settings
from tests.helpers.approvals import key, propose
from tests.helpers.auth import login_as

pytestmark = pytest.mark.integration


def _other(client: AsyncClient) -> AsyncClient:
    """A second client sharing the ASGI transport but not the cookie jar."""
    return AsyncClient(transport=client._transport, base_url="http://testserver")  # noqa: SLF001


async def test_the_proposer_can_neither_decide_nor_apply_their_own_proposal(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(
        db_session, session_factory, settings, client, app, requested_by="supervisor@demo.test"
    )

    denied = await proposal.proposer.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "SELF_APPROVAL_DENIED"

    async with session_factory() as session:
        denials = (
            await session.scalars(
                sa.select(AuditEvent).where(
                    AuditEvent.target_id == str(proposal.recommendation_id),
                    AuditEvent.outcome == AuditOutcome.DENIED.value,
                )
            )
        ).all()
        assert [row.action for row in denials] == ["recommendation.decided"]
        assert str(proposal.proposer.user.id) == denials[0].actor_id

        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.status == RecommendationStatus.PROPOSED.value

    other_client = _other(client)
    supervisor_b = await login_as(other_client, session_factory, "supervisor.b@demo.test")
    approved = await supervisor_b.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/decision",
        json={"decision": "APPROVED", "proposal_hash": proposal.proposal_hash},
    )
    assert approved.status_code == 200, approved.text

    self_apply = await proposal.proposer.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply"),
    )
    assert self_apply.status_code == 403, self_apply.text
    assert self_apply.json()["error"]["code"] == "SELF_APPROVAL_DENIED"

    async with session_factory() as session:
        denials = (
            await session.scalars(
                sa.select(AuditEvent).where(
                    AuditEvent.target_id == str(proposal.recommendation_id),
                    AuditEvent.outcome == AuditOutcome.DENIED.value,
                )
            )
        ).all()
        assert sorted(row.action for row in denials) == [
            "recommendation.applied",
            "recommendation.decided",
        ]

    applied = await supervisor_b.post(
        f"/api/v1/recommendations/{proposal.recommendation_id}/apply",
        json={"proposal_hash": proposal.proposal_hash},
        headers=key("apply-b"),
    )
    assert applied.status_code == 200, applied.text
    await other_client.aclose()


async def test_only_supervisors_of_the_own_factory_may_decide(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    proposal = await propose(db_session, session_factory, settings, client, app)
    body = {"decision": "APPROVED", "proposal_hash": proposal.proposal_hash}
    url = f"/api/v1/recommendations/{proposal.recommendation_id}/decision"

    # The viewer has a KTN role but not `recommendation:decide`; the planner is
    # both unprivileged *and* the proposer, and the permission check comes
    # first, so both see the same code.
    for email, code in (("viewer@demo.test", "FORBIDDEN"), ("planner@demo.test", "FORBIDDEN")):
        other_client = _other(client)
        actor = await login_as(other_client, session_factory, email)
        response = await actor.post(url, json=body)
        assert response.status_code == 403, f"{email}: {response.text}"
        assert response.json()["error"]["code"] == code, email
        await other_client.aclose()

    foreign_client = _other(client)
    foreign = await login_as(foreign_client, session_factory, "byg.planner@demo.test")
    response = await foreign.post(url, json=body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"

    detail = await foreign.get(f"/api/v1/recommendations/{proposal.recommendation_id}")
    assert detail.status_code == 404, detail.text
    await foreign_client.aclose()

    async with session_factory() as session:
        recommendation = await session.get(Recommendation, proposal.recommendation_id)
        assert recommendation is not None
        assert recommendation.status == RecommendationStatus.PROPOSED.value
