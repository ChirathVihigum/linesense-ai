"""The four-agent graph end to end, through the public API.

RM, IE and quality start together; planning waits for RM *and* IE; the run
finalizes into one canonical, evidence-backed order report. The degraded
paths (a provider outage, and the provider switched off entirely) must still
produce that report, from deterministic state only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import AI_UNAVAILABLE_WARNING
from app.db.models import AgentTask, Order, RunEvent
from app.domain.vocab import RecommendationStatus, RunStatus
from app.llm import LLMUnavailableError
from app.llm.fixture_client import FixtureLLMClient
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from app.settings import Settings
from tests.helpers.auth import login_as
from tests.helpers.worker import drain, drain_with_backoff

pytestmark = pytest.mark.integration

ROUND_ZERO_RECIPIENTS = {"rm", "ie", "quality"}


def _transport(app: Any) -> ASGITransport:
    return ASGITransport(app=app, raise_app_exceptions=False)


async def _seeded_order(db_session: AsyncSession, settings: Settings) -> Order:
    anchor = datetime.now(ZoneInfo("Asia/Colombo")).date()
    await seed_demo(db_session, anchor_date=anchor, issuer=settings.oidc_issuer)
    order = await db_session.scalar(sa.select(Order).where(Order.external_ref == DEMO_ORDER_REF))
    assert order is not None
    return order


async def _request_analysis(client: AsyncClient, order: Order) -> uuid.UUID:
    accepted = await client.post(
        f"/api/v1/orders/{order.id}/analyses",
        json={"expected_order_version": order.version},
        headers={"Idempotency-Key": f"four-{uuid.uuid4().hex}"},
    )
    assert accepted.status_code == 202, accepted.text
    return uuid.UUID(accepted.json()["run_id"])


async def _events(session: AsyncSession, run_id: uuid.UUID) -> list[RunEvent]:
    return list(
        (
            await session.scalars(
                sa.select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)
            )
        ).all()
    )


# --------------------------------------------------------------------------
# The full four-agent run
# --------------------------------------------------------------------------


async def test_four_agents_run_and_produce_one_canonical_report(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    order = await _seeded_order(db_session, settings)
    order_id = order.id
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    run_id = await _request_analysis(planner, order)
    await drain(session_factory, settings, transport=_transport(app))

    detail = await planner.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == RunStatus.AWAITING_REVIEW.value
    assert [(task["recipient"], task["round"]) for task in body["tasks"]] == [
        ("rm", 0),
        ("ie", 0),
        ("quality", 0),
        ("planning", 0),
        ("planning", 1),
        ("rm", 1),
    ]

    async with session_factory() as session:
        events = await _events(session, run_id)
        types = [event.event_type for event in events]
        assert types.count("task.dispatched") == 6
        assert types.count("task.completed") == 6

        # The three round-0 tasks went out in one advance step: consecutive
        # event ids, before anything completed.
        dispatched = [event for event in events if event.event_type == "task.dispatched"]
        first_three = dispatched[:3]
        assert {event.payload["recipient"] for event in first_three} == ROUND_ZERO_RECIPIENTS
        assert [event.id for event in first_three] == [
            first_three[0].id,
            first_three[0].id + 1,
            first_three[0].id + 2,
        ]
        first_completed = types.index("task.completed")
        assert types.index("task.dispatched") + 2 < first_completed

        tasks = {
            (task.recipient, task.round): task
            for task in (
                await session.scalars(sa.select(AgentTask).where(AgentTask.run_id == run_id))
            ).all()
        }
        planning0 = tasks[("planning", 0)]
        dependency_ids = {
            uuid.UUID(ref["id"])
            for ref in planning0.envelope["input_refs"]
            if ref["type"] == "agent_result"
        }
        assert dependency_ids == {tasks[("rm", 0)].id, tasks[("ie", 0)].id}

    # The report: deterministic state, whatever the agents wrote.
    report = body["report"]
    assert report is not None
    assert report["order"]["external_ref"] == DEMO_ORDER_REF
    assert "MATERIAL_SHORTAGE" in [blocker["code"] for blocker in report["blockers"]]
    assert all(blocker["severity"] == "critical" for blocker in report["blockers"])

    by_agent = {summary["agent"]: summary for summary in report["agent_summaries"]}
    assert set(by_agent) == {"rm", "ie", "quality", "planning"}
    assert "BOTTLENECK_OPERATION" in by_agent["ie"]["finding_codes"]
    assert "BOTTLENECK_OPERATION" not in [blocker["code"] for blocker in report["blockers"]]
    assert "NOT_INSPECTED" in by_agent["quality"]["finding_codes"]
    assert "DEMO_POLICY" in by_agent["quality"]["finding_codes"]

    assert report["states"]["quality"] == "NOT_INSPECTED"
    assert report["states"]["material"] == "SHORTAGE"
    assert report["states"]["analysis"] == RunStatus.AWAITING_REVIEW.value
    assert report["shipment"]["eligible"] is False
    assert report["shipment"]["source"] == "Calculated from records"
    assert report["evidence"]
    assert {item["agent"] for item in report["evidence"]} >= {"rm", "ie", "quality", "planning"}

    assert [row["status"] for row in body["recommendations"]] == [
        RecommendationStatus.PROPOSED.value
    ]
    assert report["recommendation"]["id"] == body["recommendations"][0]["id"]
    assert report["recommendation"]["status"] == RecommendationStatus.PROPOSED.value

    # The same report reaches the order detail, fresh (nothing changed since).
    order_detail = await planner.get(f"/api/v1/orders/{order_id}")
    assert order_detail.status_code == 200, order_detail.text
    latest = order_detail.json()["latest_report"]
    assert latest is not None
    assert latest["stale"] is False
    assert latest["blockers"] == report["blockers"]


async def test_the_order_report_is_marked_stale_once_the_order_moves_on(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    order = await _seeded_order(db_session, settings)
    order_id = order.id
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    await _request_analysis(planner, order)
    await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session, session.begin():
        stored = await session.get(Order, order_id)
        assert stored is not None
        stored.version += 1

    detail = await planner.get(f"/api/v1/orders/{order_id}")
    assert detail.status_code == 200
    assert detail.json()["latest_report"]["stale"] is True


# --------------------------------------------------------------------------
# Degraded paths
# --------------------------------------------------------------------------


class _OutageLLM(FixtureLLMClient):
    async def complete(self, **kwargs: Any) -> Any:
        raise LLMUnavailableError("the provider is unavailable")


async def test_a_provider_outage_still_produces_a_deterministic_report(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = await _seeded_order(db_session, settings)
    await db_session.commit()

    monkeypatch.setattr(
        "app.orchestration.executor.build_llm_client", lambda _settings: _OutageLLM()
    )
    planner = await login_as(client, session_factory, "planner@demo.test")
    run_id = await _request_analysis(planner, order)
    await drain_with_backoff(session_factory, settings, transport=_transport(app))

    body = (await planner.get(f"/api/v1/runs/{run_id}")).json()
    assert body["status"] in (RunStatus.AWAITING_REVIEW.value, RunStatus.DEGRADED.value)
    assert body["completed_at"] is not None
    # Two retries after the initial attempt, then the deterministic fallback.
    # Those attempts reserve model calls, so once the run's budget is spent the
    # remaining tasks degrade on their first attempt instead of retrying.
    attempts = {(task["recipient"], task["round"]): task["attempt"] for task in body["tasks"]}
    assert attempts[("rm", 0)] == 3
    assert max(attempts.values()) == 3
    assert all(result["status"] == "DEGRADED" for result in body["results"])
    assert all(result["summary_source"] == "deterministic" for result in body["results"])

    report = body["report"]
    assert report is not None
    assert report["degraded"] is True
    assert "PROVIDER_UNAVAILABLE" in report["degraded_reasons"]
    assert all(summary["summary_source"] != "model" for summary in report["agent_summaries"])
    codes = {code for summary in report["agent_summaries"] for code in summary["finding_codes"]}
    assert {"MATERIAL_SHORTAGE", "BOTTLENECK_OPERATION", "NOT_INSPECTED"} <= codes
    assert report["shipment"]["eligible"] is False


async def test_a_disabled_provider_completes_the_run_deterministically(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: AsyncClient,
    app: Any,
) -> None:
    order = await _seeded_order(db_session, settings)
    await db_session.commit()

    planner = await login_as(client, session_factory, "planner@demo.test")
    run_id = await _request_analysis(planner, order)
    disabled = settings.model_copy(update={"llm_provider": "disabled"})
    await drain(session_factory, disabled, transport=_transport(app))

    body = (await planner.get(f"/api/v1/runs/{run_id}")).json()
    assert body["status"] == RunStatus.AWAITING_REVIEW.value
    assert all(result["status"] == "DEGRADED" for result in body["results"])
    assert all(AI_UNAVAILABLE_WARNING in " ".join(result["warnings"]) for result in body["results"])
    assert all(
        result["execution_metadata"]["degraded_reason"] == "LLM_DISABLED"
        for result in body["results"]
    )

    assert [row["generated_by"] for row in body["recommendations"]] == ["deterministic"]
    report = body["report"]
    assert report["degraded"] is True
    assert report["degraded_reasons"] == ["LLM_DISABLED"]
    assert report["recommendation"]["source_label"].startswith("Deterministic")
