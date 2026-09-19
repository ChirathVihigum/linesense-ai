"""The ``agent.execute`` handler: fencing, single results, policy, exhaustion."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import AgentContext, AgentExecutionError, Assessment, BaseAgent
from app.agents.registry import temporary_agent
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    AnalysisRun,
    Factory,
    Job,
    Organization,
    RunEvent,
    RunSnapshot,
)
from app.domain.vocab import JobStatus, RunStatus, TaskStatus
from app.jobs.handlers import build_registry
from app.jobs.queue import LeaseLostError, claim, enqueue
from app.jobs.worker import JobContext, Worker
from app.orchestration.executor import AGENT_EXECUTE_JOB, execute_agent_task
from app.orchestration.protocol import (
    AgentErrorCode,
    DataQuality,
    EvidenceRef,
    Finding,
    Metric,
)
from app.settings import Settings
from tests.factories import make_factory, make_order, make_org
from tests.helpers.agents import make_requester, make_run_with_snapshot

pytestmark = pytest.mark.integration


class FakeRmAgent(BaseAgent):
    name = "rm"
    prompt_version = "fake-rm-v1"
    goal = "Assess material readiness."
    system_prompt = "You are a deterministic test agent."

    async def assess(self, ctx: AgentContext) -> Assessment:
        return Assessment(
            summary="Deterministic assessment for the executor test.",
            findings=[
                Finding(
                    finding_id="rm-1",
                    severity="warning",
                    code="BELOW_REORDER_POINT",
                    message="Stock is below the reorder point.",
                    evidence_ids=["ev-1"],
                    source="deterministic",
                )
            ],
            metrics=[Metric(name="coverable_units", value=873, unit="units")],
            evidence_refs=[
                EvidenceRef(
                    evidence_id="ev-1", kind="calculation", description="coverable_units = 873"
                )
            ],
            data_quality=DataQuality(complete=True, missing=[], notes=[]),
        )


class OutageRmAgent(FakeRmAgent):
    """Fails its first assessment with a provider outage, then succeeds.

    That is exactly the shape of a real outage followed by the exhaustion
    hook's deterministic re-run.
    """

    calls = 0

    async def assess(self, ctx: AgentContext) -> Assessment:
        OutageRmAgent.calls += 1
        if OutageRmAgent.calls == 1:
            raise AgentExecutionError(
                AgentErrorCode.PROVIDER_UNAVAILABLE, retryable=True, detail="provider is down"
            )
        return await super().assess(ctx)


async def _prepare_run(
    session: AsyncSession, **run_overrides: Any
) -> tuple[AnalysisRun, RunSnapshot, Organization, Factory]:
    organization = await make_org(session)
    factory = await make_factory(session, organization=organization)
    order = await make_order(session, organization=organization, factory=factory)
    requester = await make_requester(session, organization, factory)
    run, snapshot = await make_run_with_snapshot(
        session, order=order, requested_by=requester, **run_overrides
    )
    return run, snapshot, organization, factory


async def _create_task(
    session: AsyncSession, run: AnalysisRun, *, max_attempts: int = 3
) -> tuple[AgentTask, uuid.UUID]:
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
        envelope={
            "schema_version": "1.0",
            "message_id": str(uuid.uuid4()),
            "run_id": str(run.id),
            "parent_task_id": None,
            "organization_id": str(run.organization_id),
            "factory_id": str(run.factory_id),
            "order_id": str(run.order_id),
            "snapshot_id": str(run.snapshot_id),
            "sender": "orchestrator",
            "recipient": "rm",
            "task_type": "assess_material_readiness",
            "idempotency_key": f"{run.id}:rm:{run.snapshot_id}:round-0",
            "round": 0,
            "deadline_at": run.deadline_at.isoformat(),
            "input_refs": [{"type": "snapshot", "id": str(run.snapshot_id), "version": None}],
            "constraints": {"max_tool_calls": 4, "read_only": True},
            "trace_id": run.trace_id,
        },
        deadline_at=run.deadline_at,
        max_attempts=max_attempts,
    )
    session.add(task)
    await session.flush()
    job_id = await enqueue(
        session,
        queue="agent",
        job_type=AGENT_EXECUTE_JOB,
        payload={"task_id": str(task.id)},
        dedupe_key=f"task:{task.id}",
        max_attempts=max_attempts,
    )
    assert job_id is not None
    return task, job_id


def _worker(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> Worker:
    return Worker(
        registry=build_registry(settings),
        session_factory=session_factory,
        settings=settings,
        queues=["agent"],
        concurrency=1,
        lease_seconds=30,
        heartbeat_seconds=10,
        poll_interval=0.05,
        worker_id=f"test-{uuid.uuid4().hex[:8]}",
        reconcile_seconds=None,
    )


async def test_task_runs_once_and_advances_the_run(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, _snapshot, _org, _factory = await _prepare_run(db_session)
    task, job_id = await _create_task(db_session, run)
    await db_session.commit()

    with temporary_agent("rm", FakeRmAgent):
        assert await _worker(session_factory, settings).run_once() is True

    async with session_factory() as session:
        results = (
            await session.scalars(
                select(AgentResultRecord).where(AgentResultRecord.task_id == task.id)
            )
        ).all()
        assert len(results) == 1
        result = results[0]
        assert result.status == "SUCCEEDED"
        assert result.payload["agent"] == "rm"
        assert result.payload["execution_metadata"]["provider"] == "fixture"
        assert result.payload["summary_source"] == "model"
        assert [f["code"] for f in result.payload["findings"]][0] == "BELOW_REORDER_POINT"

        stored_task = await session.get(AgentTask, task.id)
        assert stored_task is not None
        assert stored_task.status == TaskStatus.SUCCEEDED.value
        assert stored_task.completed_at is not None
        assert stored_task.attempt == 1

        job = await session.get(Job, job_id)
        assert job is not None and job.status == JobStatus.DONE.value

        event = await session.scalar(
            select(RunEvent).where(
                RunEvent.run_id == run.id, RunEvent.event_type == "task.completed"
            )
        )
        assert event is not None
        assert event.actor == "agent:rm"
        assert event.payload["status"] == "SUCCEEDED"
        assert event.payload["finding_codes"] == ["BELOW_REORDER_POINT"]

        advance = await session.scalar(
            select(Job).where(Job.dedupe_key == f"run:{run.id}:after:{task.id}")
        )
        assert advance is not None
        assert (advance.queue, advance.job_type) == ("orchestrator", "orchestrator.advance")


async def test_lease_lost_before_commit_writes_nothing(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, _snapshot, _org, _factory = await _prepare_run(db_session)
    task, job_id = await _create_task(db_session, run)
    await db_session.commit()

    claimed = await claim(session_factory, queues=["agent"], worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    # Another worker reclaims the job while this one is still working.
    async with session_factory() as session, session.begin():
        job = await session.get(Job, job_id)
        assert job is not None
        job.lease_token = uuid.uuid4()

    ctx = JobContext(
        job=claimed, session_factory=session_factory, settings=settings, worker_id="worker-a"
    )
    with temporary_agent("rm", FakeRmAgent), pytest.raises(LeaseLostError):
        await execute_agent_task(ctx)

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentResultRecord)
                .where(AgentResultRecord.task_id == task.id)
            )
            == 0
        )
        job = await session.get(Job, job_id)
        assert job is not None and job.status != JobStatus.DONE.value


async def test_second_delivery_does_not_create_a_second_result(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, _snapshot, _org, _factory = await _prepare_run(db_session)
    task, _job_id = await _create_task(db_session, run)
    await db_session.commit()

    with temporary_agent("rm", FakeRmAgent):
        assert await _worker(session_factory, settings).run_once() is True
        async with session_factory() as session, session.begin():
            await enqueue(
                session,
                queue="agent",
                job_type=AGENT_EXECUTE_JOB,
                payload={"task_id": str(task.id)},
                dedupe_key=f"task:{task.id}:redelivery",
            )
        assert await _worker(session_factory, settings).run_once() is True

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentResultRecord)
                .where(AgentResultRecord.task_id == task.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.run_id == run.id, RunEvent.event_type == "task.completed")
            )
            == 1
        )


async def test_requester_without_permission_fails_the_task(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    organization = await make_org(db_session)
    factory = await make_factory(db_session, organization=organization)
    order = await make_order(db_session, organization=organization, factory=factory)
    # No membership/role for the requester: the run's user lost access.
    run, _snapshot = await make_run_with_snapshot(db_session, order=order)
    task, job_id = await _create_task(db_session, run)
    await db_session.commit()

    with temporary_agent("rm", FakeRmAgent):
        assert await _worker(session_factory, settings).run_once() is True

    async with session_factory() as session:
        stored_task = await session.get(AgentTask, task.id)
        assert stored_task is not None
        assert stored_task.status == TaskStatus.FAILED.value
        assert stored_task.error_code == AgentErrorCode.POLICY_DENIED.value
        result = await session.scalar(
            select(AgentResultRecord).where(AgentResultRecord.task_id == task.id)
        )
        assert result is not None and result.status == "FAILED"
        assert result.payload["error_code"] == AgentErrorCode.POLICY_DENIED.value
        job = await session.get(Job, job_id)
        assert job is not None and job.status == JobStatus.DONE.value


async def test_retryable_failure_reschedules_the_job(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, _snapshot, _org, _factory = await _prepare_run(db_session)
    task, job_id = await _create_task(db_session, run)
    await db_session.commit()

    OutageRmAgent.calls = 0
    with temporary_agent("rm", OutageRmAgent):
        assert await _worker(session_factory, settings).run_once() is True

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.READY.value
        assert job.attempt == 1
        assert job.last_error is not None
        assert AgentErrorCode.PROVIDER_UNAVAILABLE.value in job.last_error
        stored_task = await session.get(AgentTask, task.id)
        assert stored_task is not None
        assert stored_task.status == TaskStatus.RUNNING.value
        assert stored_task.error_code == AgentErrorCode.PROVIDER_UNAVAILABLE.value
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentResultRecord)
                .where(AgentResultRecord.task_id == task.id)
            )
            == 0
        )


async def test_exhausted_provider_outage_stores_a_degraded_deterministic_result(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, _snapshot, _org, _factory = await _prepare_run(db_session)
    task, job_id = await _create_task(db_session, run, max_attempts=1)
    await db_session.commit()

    OutageRmAgent.calls = 0
    with temporary_agent("rm", OutageRmAgent):
        # The single attempt fails with a provider outage; the exhaustion hook
        # then re-runs the agent's deterministic assessment without the model.
        assert await _worker(session_factory, settings).run_once() is True

    async with session_factory() as session:
        result = await session.scalar(
            select(AgentResultRecord).where(AgentResultRecord.task_id == task.id)
        )
        assert result is not None
        assert result.status == "DEGRADED"
        assert (
            result.payload["execution_metadata"]["degraded_reason"]
            == AgentErrorCode.PROVIDER_UNAVAILABLE.value
        )
        assert "AI explanation unavailable (provider unavailable)" in result.payload["warnings"]
        assert result.payload["summary_source"] == "deterministic"
        stored_task = await session.get(AgentTask, task.id)
        assert stored_task is not None and stored_task.status == TaskStatus.SUCCEEDED.value
        assert stored_task.error_code == AgentErrorCode.PROVIDER_UNAVAILABLE.value
        assert result.payload["error_code"] == AgentErrorCode.PROVIDER_UNAVAILABLE.value
        job = await session.get(Job, job_id)
        assert job is not None and job.status == JobStatus.FAILED.value
        advance = await session.scalar(
            select(Job).where(Job.dedupe_key == f"run:{run.id}:after:{task.id}")
        )
        assert advance is not None


async def test_cancelled_run_skips_execution(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    run, _snapshot, _org, _factory = await _prepare_run(
        db_session, status=RunStatus.CANCELLED.value
    )
    task, job_id = await _create_task(db_session, run)
    await db_session.commit()

    with temporary_agent("rm", FakeRmAgent):
        assert await _worker(session_factory, settings).run_once() is True

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentResultRecord)
                .where(AgentResultRecord.task_id == task.id)
            )
            == 0
        )
        stored_task = await session.get(AgentTask, task.id)
        assert stored_task is not None and stored_task.status == TaskStatus.PENDING.value
        job = await session.get(Job, job_id)
        assert job is not None and job.status == JobStatus.DONE.value
