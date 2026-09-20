"""The whole vertical slice, driven through the public API.

A planner asks for an analysis of the seeded demo order; the worker runs the
orchestrator and all four agents; the planner then sees the run, its events and
the recommendation that is waiting for review. The four-agent graph itself is
covered by ``test_four_agent_flow.py``; this test guards the planner's journey.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.runs import FIXTURE_LABEL
from app.db.models import AgentResultRecord, AgentTask, Factory, Order, Recommendation
from app.domain.vocab import RecommendationStatus, RunStatus
from app.orchestration.protocol import AgentResult
from app.seed import scenario as demo
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from app.settings import Settings
from tests.helpers.auth import login_as
from tests.helpers.worker import drain

pytestmark = pytest.mark.integration

EXPECTED_EVENT_SEQUENCE = [
    "run.created",
    "run.started",
    # RM, IE and quality round 0 are dispatched together when the run starts.
    "task.dispatched",
    "task.dispatched",
    "task.dispatched",
    "task.completed",
    "task.completed",
    "task.completed",
    "task.dispatched",  # planning round 0, once RM and IE are terminal
    "task.completed",
    "orchestrator.replan",
    "task.dispatched",  # planning round 1
    "task.completed",
    "task.dispatched",  # RM round 1
    "task.completed",
    "run.report",
    "run.finalized",
]


async def test_planner_requests_an_analysis_and_gets_a_recommendation(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    anchor = datetime.now(ZoneInfo("Asia/Colombo")).date()
    await seed_demo(db_session, anchor_date=anchor, issuer=settings.oidc_issuer)
    order = await db_session.scalar(sa.select(Order).where(Order.external_ref == DEMO_ORDER_REF))
    assert order is not None
    factory = await db_session.get(Factory, order.factory_id)
    assert factory is not None and factory.code == "KTN"
    order_id, order_version = order.id, order.version
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    accepted = await planner.post(
        f"/api/v1/orders/{order_id}/analyses",
        json={"expected_order_version": order_version},
        headers={"Idempotency-Key": f"flow-{uuid.uuid4().hex}"},
    )
    assert accepted.status_code == 202, accepted.text
    run_id = uuid.UUID(accepted.json()["run_id"])
    assert accepted.json()["status"] == RunStatus.QUEUED.value

    await drain(
        session_factory, settings, transport=ASGITransport(app=app, raise_app_exceptions=False)
    )

    detail = await planner.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == RunStatus.AWAITING_REVIEW.value
    assert body["llm"]["is_fixture"] is True
    assert body["llm"]["label"] == FIXTURE_LABEL
    assert body["order"]["external_ref"] == DEMO_ORDER_REF
    assert [(task["recipient"], task["round"]) for task in body["tasks"]] == [
        ("rm", 0),
        ("ie", 0),
        ("quality", 0),
        ("planning", 0),
        ("planning", 1),
        ("rm", 1),
    ]
    assert all(task["status"] == "SUCCEEDED" for task in body["tasks"])

    events = await planner.get(f"/api/v1/runs/{run_id}/events")
    assert events.status_code == 200
    assert [event["event_type"] for event in events.json()] == EXPECTED_EVENT_SEQUENCE
    replan = next(event for event in events.json() if event["event_type"] == "orchestrator.replan")
    assert str(replan["payload"]["reason"]).startswith("MATERIAL_SHORTAGE_CONFLICT")

    async with session_factory() as session:
        revision = await session.scalar(
            sa.select(AgentTask).where(
                AgentTask.run_id == run_id, AgentTask.recipient == "planning", AgentTask.round == 1
            )
        )
        assert revision is not None
        record = await session.scalar(
            sa.select(AgentResultRecord).where(AgentResultRecord.task_id == revision.id)
        )
        assert record is not None
        result = AgentResult.model_validate(record.payload)
        assert "REVISED_FOR_MATERIAL" in [finding.code for finding in result.findings]
        # Six agent tasks share the run's 12 model calls, and the four round-0
        # and round-0-dependent tasks spend them: the revision falls back to its
        # deterministic assessment (BUDGET_EXCEEDED) rather than skipping work.
        assert result.summary_source == "deterministic"
        assert result.execution_metadata.degraded_reason == "BUDGET_EXCEEDED"

        # Task 14 adds GET /factories/{code}/recommendations; until then the
        # PROPOSED row itself is the contract.
        proposed = (
            await session.scalars(
                sa.select(Recommendation).where(
                    Recommendation.factory_id == factory.id,
                    Recommendation.status == RecommendationStatus.PROPOSED.value,
                )
            )
        ).all()
        assert len(proposed) == 1
        recommendation = proposed[0]
        assert recommendation.run_id == run_id
        assert recommendation.order_id == order_id
        assert recommendation.kind == "ALLOCATION_AND_RESERVATION"
        assert recommendation.generated_by == "deterministic"
        allocated = sum(
            (Decimal(row["units"]) for row in recommendation.proposal["allocations"]), Decimal(0)
        )
        assert allocated == demo.DEMO_EXPECTED_COVERABLE_UNITS
        reservation = next(
            row
            for row in recommendation.proposal["reservations"]
            if row["material_code"] == demo.DEMO_BOM_MATERIAL_CODE
        )
        assert reservation["quantity"] == "1099.98"

    assert [row["status"] for row in body["recommendations"]] == [
        RecommendationStatus.PROPOSED.value
    ]
    assert body["recommendations"][0]["generated_by"] == "deterministic"
    assert body["replan_count"] == 1

    report = body["report"]
    assert report is not None
    assert report["order"]["external_ref"] == DEMO_ORDER_REF
    assert report["recommendation"]["id"] == body["recommendations"][0]["id"]
