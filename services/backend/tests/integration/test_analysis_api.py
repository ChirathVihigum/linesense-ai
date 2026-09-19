"""The analysis API: requesting runs, run detail, events, cancel and retry."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.runs import FIXTURE_LABEL
from app.db.models import (
    AgentTask,
    AnalysisRun,
    AuditEvent,
    Factory,
    Job,
    Material,
    MaterialBalance,
    Order,
    Organization,
    Recommendation,
    RunEvent,
    RunSnapshot,
)
from app.domain.vocab import (
    JobStatus,
    ProductionState,
    RecommendationStatus,
    RunStatus,
    TaskStatus,
)
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from tests.factories import make_order, make_recommendation, make_run
from tests.helpers.auth import AuthedClient, login_as

pytestmark = pytest.mark.integration

ANCHOR_DATE = date(2026, 1, 5)


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"analysis-{uuid.uuid4().hex}"}


async def _demo_scope(session: AsyncSession) -> tuple[Organization, Factory]:
    organization = await session.scalar(
        select(Organization).where(Organization.slug == "demo-apparel")
    )
    assert organization is not None
    factory = await session.scalar(
        select(Factory).where(Factory.organization_id == organization.id, Factory.code == "KTN")
    )
    assert factory is not None
    return organization, factory


async def _order_in_demo_factory(session: AsyncSession, **overrides: Any) -> Order:
    organization, factory = await _demo_scope(session)
    overrides.setdefault("production_state", ProductionState.VALIDATED.value)
    return await make_order(session, organization=organization, factory=factory, **overrides)


async def _planner(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> AuthedClient:
    return await login_as(client, session_factory, "planner@demo.test")


async def test_planner_starts_a_run_with_a_snapshot_of_the_demo_order(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_demo(
        db_session, anchor_date=ANCHOR_DATE, issuer="https://idp.analysis-api-test.example"
    )
    await db_session.commit()
    organization, factory = await _demo_scope(db_session)
    order = await db_session.scalar(
        select(Order).where(
            Order.organization_id == organization.id, Order.external_ref == DEMO_ORDER_REF
        )
    )
    assert order is not None
    material = await db_session.scalar(
        select(Material).where(Material.organization_id == organization.id, Material.code == "M01")
    )
    assert material is not None
    balance = await db_session.scalar(
        select(MaterialBalance).where(
            MaterialBalance.factory_id == factory.id, MaterialBalance.material_id == material.id
        )
    )
    assert balance is not None

    planner = await _planner(client, session_factory)
    response = await planner.post(
        f"/api/v1/orders/{order.id}/analyses",
        json={"expected_order_version": order.version},
        headers=_key(),
    )
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["run_id"])
    assert response.json()["status"] == RunStatus.QUEUED.value

    run = await db_session.get(AnalysisRun, run_id)
    assert run is not None
    assert run.status == RunStatus.QUEUED.value
    assert run.llm_provider == "fixture"
    assert run.snapshot_id is not None
    snapshot = await db_session.get(RunSnapshot, run.snapshot_id)
    assert snapshot is not None
    assert snapshot.input_versions["material_balances"][str(balance.id)] == balance.version
    assert snapshot.input_versions["order"] == {str(order.id): order.version}
    assert snapshot.data["order"]["external_ref"] == DEMO_ORDER_REF
    assert snapshot.data["bom"]["lines"], "the demo order's BOM is part of the snapshot"

    job = await db_session.scalar(select(Job).where(Job.dedupe_key == f"run:{run.id}:start"))
    assert job is not None
    assert (job.queue, job.job_type) == ("orchestrator", "orchestrator.advance")
    event = await db_session.scalar(
        select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.event_type == "run.created")
    )
    assert event is not None
    audit = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "analysis.requested")
    )
    assert audit is not None and audit.run_id == run.id


async def test_stale_order_version_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(db_session)
    await db_session.commit()

    response = await planner.post(
        f"/api/v1/orders/{order.id}/analyses",
        json={"expected_order_version": order.version + 1},
        headers=_key(),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_INPUT"
    assert await db_session.scalar(select(func.count()).select_from(AnalysisRun)) == 0


async def test_a_second_run_for_the_same_order_conflicts(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(db_session)
    await db_session.commit()
    body = {"expected_order_version": order.version}

    first = await planner.post(f"/api/v1/orders/{order.id}/analyses", json=body, headers=_key())
    assert first.status_code == 202
    second = await planner.post(f"/api/v1/orders/{order.id}/analyses", json=body, headers=_key())
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "CONFLICT"
    assert first.json()["run_id"] in error["message"]


async def test_cancelled_order_cannot_be_analysed(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(
        db_session, production_state=ProductionState.CANCELLED.value
    )
    await db_session.commit()

    response = await planner.post(
        f"/api/v1/orders/{order.id}/analyses",
        json={"expected_order_version": order.version},
        headers=_key(),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_TRANSITION"


async def test_factory_run_limit_is_enforced(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(db_session)
    for _ in range(5):
        other = await _order_in_demo_factory(db_session)
        await make_run(db_session, order=other, status=RunStatus.RUNNING.value)
    await db_session.commit()

    response = await planner.post(
        f"/api/v1/orders/{order.id}/analyses",
        json={"expected_order_version": order.version},
        headers=_key(),
    )
    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "RATE_LIMITED"
    assert body["retry_after_seconds"] == 30


async def test_viewer_is_forbidden_and_other_factory_is_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await login_as(client, session_factory, "planner@demo.test")
    order = await _order_in_demo_factory(db_session)
    await db_session.commit()
    body = {"expected_order_version": order.version}

    viewer = await login_as(client, session_factory, "viewer@demo.test")
    forbidden = await viewer.post(f"/api/v1/orders/{order.id}/analyses", json=body, headers=_key())
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"

    byg_planner = await login_as(client, session_factory, "byg.planner@demo.test")
    not_found = await byg_planner.post(
        f"/api/v1/orders/{order.id}/analyses", json=body, headers=_key()
    )
    assert not_found.status_code == 404


async def test_run_detail_and_events_show_the_fixture_label(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(db_session)
    await db_session.commit()
    created = await planner.post(
        f"/api/v1/orders/{order.id}/analyses",
        json={"expected_order_version": order.version},
        headers=_key(),
    )
    run_id = created.json()["run_id"]

    detail = await planner.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["llm"] == {
        "provider": "fixture",
        "model": "fixture-scripted-v1",
        "is_fixture": True,
        "label": FIXTURE_LABEL,
    }
    assert body["order"]["external_ref"] == order.external_ref
    assert body["requested_by"]["display_name"]
    assert body["model_calls_limit"] == 12
    assert body["tasks"] == [] and body["results"] == [] and body["recommendations"] == []
    assert body["report"] is None

    events = await planner.get(f"/api/v1/runs/{run_id}/events")
    assert events.status_code == 200
    assert [event["event_type"] for event in events.json()] == ["run.created"]
    first_id = events.json()[0]["id"]
    assert (await planner.get(f"/api/v1/runs/{run_id}/events?after_id={first_id}")).json() == []

    listed = await planner.get(f"/api/v1/orders/{order.id}/runs")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == run_id


async def test_cancel_stops_tasks_jobs_and_supersedes_recommendations(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(db_session)
    await db_session.commit()
    run_id = uuid.UUID(
        (
            await planner.post(
                f"/api/v1/orders/{order.id}/analyses",
                json={"expected_order_version": order.version},
                headers=_key(),
            )
        ).json()["run_id"]
    )
    run = await db_session.get(AnalysisRun, run_id)
    assert run is not None
    task = AgentTask(
        id=uuid.uuid4(),
        run_id=run.id,
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        message_id=uuid.uuid4(),
        sender="orchestrator",
        recipient="rm",
        task_type="assess_material_readiness",
        round=0,
        idempotency_key=f"{run.id}:rm:{run.snapshot_id}:round-0",
        status=TaskStatus.PENDING.value,
        envelope={},
        deadline_at=run.deadline_at,
    )
    db_session.add(task)
    await db_session.flush()
    db_session.add(
        Job(
            id=uuid.uuid4(),
            queue="agent",
            job_type="agent.execute",
            payload={"task_id": str(task.id)},
            dedupe_key=f"task:{task.id}",
            status=JobStatus.READY.value,
        )
    )
    recommendation = await make_recommendation(db_session, run=run)
    task_id, recommendation_id = task.id, recommendation.id
    await db_session.commit()

    response = await planner.post(f"/api/v1/runs/{run_id}/cancel", headers=_key())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == RunStatus.CANCELLED.value

    db_session.expire_all()  # read what the request committed, not the cache
    cancelled_run = await db_session.get(AnalysisRun, run_id)
    assert cancelled_run is not None and cancelled_run.status == RunStatus.CANCELLED.value
    assert cancelled_run.completed_at is not None
    cancelled_task = await db_session.get(AgentTask, task_id)
    assert cancelled_task is not None and cancelled_task.status == TaskStatus.CANCELLED.value
    job = await db_session.scalar(select(Job).where(Job.dedupe_key == f"task:{task_id}"))
    assert job is not None and job.status == JobStatus.CANCELLED.value
    stored = await db_session.get(Recommendation, recommendation_id)
    assert stored is not None
    assert stored.status == RecommendationStatus.SUPERSEDED.value
    assert stored.superseded_reason == "RUN_CANCELLED"
    cancelled_event = await db_session.scalar(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.event_type == "run.cancelled")
    )
    assert cancelled_event is not None


async def test_retry_is_only_allowed_from_terminal_states(
    client: AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    planner = await _planner(client, session_factory)
    order = await _order_in_demo_factory(db_session)
    run = await make_run(db_session, order=order, status=RunStatus.RUNNING.value)
    await db_session.commit()

    too_early = await planner.post(f"/api/v1/runs/{run.id}/retry", headers=_key())
    assert too_early.status_code == 409
    assert too_early.json()["error"]["code"] == "INVALID_TRANSITION"

    run.status = RunStatus.FAILED.value
    await db_session.commit()

    retried = await planner.post(f"/api/v1/runs/{run.id}/retry", headers=_key())
    assert retried.status_code == 202, retried.text
    new_run_id = uuid.UUID(retried.json()["run_id"])
    assert new_run_id != run.id
    new_run = await db_session.get(AnalysisRun, new_run_id)
    assert new_run is not None
    assert new_run.order_id == order.id
    assert new_run.snapshot_id is not None
