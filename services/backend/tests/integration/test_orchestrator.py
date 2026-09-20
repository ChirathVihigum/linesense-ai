"""The orchestrator: the RM/planning graph, the targeted replan and finalization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import AgentContext, AgentExecutionError, Assessment
from app.agents.registry import temporary_agent
from app.agents.rm import RMAgent
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    AnalysisRun,
    Factory,
    Job,
    Material,
    MaterialBalance,
    Notification,
    Order,
    Organization,
    Recommendation,
    RunEvent,
)
from app.domain.vocab import JobStatus, RecommendationStatus, RunStatus, TaskStatus
from app.jobs.queue import claim, enqueue
from app.jobs.reconcile import reconcile_once
from app.llm import LLMUnavailableError
from app.llm.fixture_client import FixtureLLMClient
from app.orchestration.executor import AGENT_EXECUTE_JOB, ORCHESTRATOR_ADVANCE_JOB
from app.orchestration.orchestrator import needs_replan
from app.orchestration.protocol import AgentErrorCode, AgentResult
from app.seed import scenario as demo
from app.seed.generator import DEMO_ORDER_REF, seed_demo
from app.settings import Settings
from tests.factories import make_order
from tests.helpers.agents import make_requester, make_run_with_snapshot
from tests.helpers.worker import drain, make_worker

pytestmark = pytest.mark.integration

TEST_ISSUER = "https://idp.orchestrator-test.example"


def _anchor_date():  # noqa: ANN202
    return datetime.now(ZoneInfo("Asia/Colombo")).date()


async def _seed(session: AsyncSession) -> tuple[Order, Organization, Factory]:
    await seed_demo(session, anchor_date=_anchor_date(), issuer=TEST_ISSUER)
    order = await session.scalar(sa.select(Order).where(Order.external_ref == DEMO_ORDER_REF))
    assert order is not None
    organization = await session.get(Organization, order.organization_id)
    factory = await session.get(Factory, order.factory_id)
    assert organization is not None and factory is not None
    return order, organization, factory


async def _start_run(
    session: AsyncSession, order: Order, organization: Organization, factory: Factory, **overrides
) -> AnalysisRun:
    requester = await make_requester(session, organization, factory)
    run, _snapshot = await make_run_with_snapshot(
        session, order=order, requested_by=requester, **overrides
    )
    await enqueue(
        session,
        queue="orchestrator",
        job_type=ORCHESTRATOR_ADVANCE_JOB,
        payload={"run_id": str(run.id)},
        dedupe_key=f"run:{run.id}:start",
    )
    return run


def _transport(app: Any) -> ASGITransport:
    return ASGITransport(app=app, raise_app_exceptions=False)


async def _drain_with_backoff(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    transport: Any,
    rounds: int = 8,
) -> int:
    """Drain, then pull every backed-off retry forward, until nothing is left.

    A retryable agent failure schedules its next attempt seconds into the
    future; the test must not sleep through the backoff.
    """
    total = 0
    for _ in range(rounds):
        total += await drain(session_factory, settings, transport=transport)
        async with session_factory() as session, session.begin():
            released = await session.execute(
                sa.update(Job)
                .where(Job.status == JobStatus.READY.value, Job.available_at > sa.func.now())
                .values(available_at=sa.func.now())
            )
        if released.rowcount == 0:
            return total
    raise AssertionError("jobs kept being rescheduled")


async def _tasks(session: AsyncSession, run_id: uuid.UUID) -> list[AgentTask]:
    return list(
        (
            await session.scalars(
                sa.select(AgentTask)
                .where(AgentTask.run_id == run_id)
                .order_by(AgentTask.created_at, AgentTask.id)
            )
        ).all()
    )


async def _result(session: AsyncSession, task: AgentTask) -> AgentResult:
    row = await session.scalar(
        sa.select(AgentResultRecord).where(AgentResultRecord.task_id == task.id)
    )
    assert row is not None, f"no result stored for {task.recipient} round {task.round}"
    return AgentResult.model_validate(row.payload)


async def _events(session: AsyncSession, run_id: uuid.UUID) -> list[str]:
    return list(
        (
            await session.scalars(
                sa.select(RunEvent.event_type)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.id)
            )
        ).all()
    )


# --------------------------------------------------------------------------
# The happy path: the demo scenario's targeted replan
# --------------------------------------------------------------------------


async def test_demo_run_replans_for_material_and_awaits_review(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order, organization, factory = await _seed(db_session)
    run = await _start_run(db_session, order, organization, factory)
    await db_session.commit()

    await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        tasks = await _tasks(session, run.id)
        assert [(task.recipient, task.round, task.task_type) for task in tasks] == [
            ("rm", 0, "assess_material_readiness"),
            ("planning", 0, "propose_allocation"),
            ("planning", 1, "revise_allocation"),
            ("rm", 1, "validate_plan_materials"),
        ]
        assert all(task.status == TaskStatus.SUCCEEDED.value for task in tasks)
        assert tasks[2].parent_task_id == tasks[1].id
        assert tasks[3].parent_task_id == tasks[2].id

        events = await _events(session, run.id)
        assert events.count("task.dispatched") == 4
        assert events.count("task.completed") == 4
        assert "orchestrator.replan" in events
        assert events[0] == "run.started"
        assert events[-1] == "run.finalized"
        replan = await session.scalar(
            sa.select(RunEvent).where(
                RunEvent.run_id == run.id, RunEvent.event_type == "orchestrator.replan"
            )
        )
        assert replan is not None
        assert str(replan.payload["reason"]).startswith("MATERIAL_SHORTAGE_CONFLICT")

        rm0 = await _result(session, tasks[0])
        coverable = next(m for m in rm0.metrics if m.name == "coverable_units")
        assert coverable.value == demo.DEMO_EXPECTED_COVERABLE_UNITS
        assert "MATERIAL_SHORTAGE" in [f.code for f in rm0.findings]

        planning1 = await _result(session, tasks[2])
        assert "REVISED_FOR_MATERIAL" in [f.code for f in planning1.findings]

        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.status == RunStatus.AWAITING_REVIEW.value
        assert stored.replan_count == 1
        assert stored.completed_at is not None

        recommendations = (
            await session.scalars(sa.select(Recommendation).where(Recommendation.run_id == run.id))
        ).all()
        assert len(recommendations) == 1
        recommendation = recommendations[0]
        assert recommendation.status == RecommendationStatus.PROPOSED.value
        assert recommendation.kind == "ALLOCATION_AND_RESERVATION"
        assert recommendation.proposed_by_agent == "planning"
        assert recommendation.proposer_user_id == stored.requested_by
        assert recommendation.expires_at > datetime.now(tz=UTC) + timedelta(hours=23)
        proposal: dict[str, Any] = dict(recommendation.proposal)
        assert proposal["option_code"] == "MATERIAL_LIMITED"
        allocated = sum((Decimal(row["units"]) for row in proposal["allocations"]), Decimal(0))
        assert allocated == demo.DEMO_EXPECTED_COVERABLE_UNITS
        demo_material = next(
            row
            for row in proposal["reservations"]
            if row["material_code"] == demo.DEMO_BOM_MATERIAL_CODE
        )
        # 873 units x 1.2 m x 1.05 wastage, capped by the 1100 m available.
        assert demo_material["quantity"] == "1099.98"
        assert demo_material["unit"] == "m"
        assert recommendation.proposal_hash
        # Exactly the slots and balances the proposal names.
        assert set(recommendation.input_versions["capacity_slots"]) == {
            row["slot_id"] for row in proposal["allocations"]
        }
        assert set(recommendation.input_versions["material_balances"]) == {
            row["balance_id"] for row in proposal["reservations"]
        }
        assert str(order.id) in recommendation.input_versions["order"]
        assert recommendation.evidence_refs["items"]
        assert {item["agent"] for item in recommendation.evidence_refs["items"]} == {
            "planning",
            "rm",
        }

        notification = await session.scalar(
            sa.select(Notification).where(Notification.link == f"/runs/{run.id}")
        )
        assert notification is not None
        assert notification.role == "supervisor"


async def test_duplicate_advance_deliveries_do_not_duplicate_tasks(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order, organization, factory = await _seed(db_session)
    run = await _start_run(db_session, order, organization, factory)
    for index in range(3):
        await enqueue(
            db_session,
            queue="orchestrator",
            job_type=ORCHESTRATOR_ADVANCE_JOB,
            payload={"run_id": str(run.id)},
            dedupe_key=f"run:{run.id}:dupe-{index}",
        )
    await db_session.commit()

    await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        tasks = await _tasks(session, run.id)
        assert len(tasks) == 4
        assert len({(task.recipient, task.round) for task in tasks}) == 4
        results = await session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentResultRecord)
            .where(AgentResultRecord.run_id == run.id)
        )
        assert results == 4
        recommendations = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Recommendation)
            .where(Recommendation.run_id == run.id)
        )
        assert recommendations == 1


async def test_no_replan_when_materials_cover_the_order(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order, organization, factory = await _seed(db_session)
    material = await db_session.scalar(
        sa.select(Material).where(
            Material.organization_id == organization.id,
            Material.code == demo.DEMO_BOM_MATERIAL_CODE,
        )
    )
    assert material is not None
    balance = await db_session.scalar(
        sa.select(MaterialBalance).where(
            MaterialBalance.factory_id == factory.id, MaterialBalance.material_id == material.id
        )
    )
    assert balance is not None
    balance.on_hand_accepted = Decimal("5000")
    balance.version += 1
    await db_session.flush()

    run = await _start_run(db_session, order, organization, factory)
    await db_session.commit()

    await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        tasks = await _tasks(session, run.id)
        assert [(task.recipient, task.round) for task in tasks] == [
            ("rm", 0),
            ("planning", 0),
            ("rm", 1),
        ]
        assert "orchestrator.replan" not in await _events(session, run.id)
        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.replan_count == 0
        assert stored.status == RunStatus.AWAITING_REVIEW.value
        recommendation = await session.scalar(
            sa.select(Recommendation).where(Recommendation.run_id == run.id)
        )
        assert recommendation is not None
        # With materials covering the whole order, the best plan is a
        # full-quantity one; which of the equivalent full options wins depends
        # on the seeded line capacities.
        assert sum(
            (Decimal(row["units"]) for row in recommendation.proposal["allocations"]), Decimal(0)
        ) == Decimal(order.quantity)
        assert recommendation.proposal["unscheduled_units"] == "0"
        assert recommendation.proposal["option_code"] in ("FULL_EARLIEST", "SINGLE_LINE")


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


class FailingRMAgent(RMAgent):
    """An RM agent whose round 0 cannot run at all (non-retryable)."""

    async def assess(self, ctx: AgentContext) -> Assessment:
        if ctx.round == 0:
            raise AgentExecutionError(
                AgentErrorCode.MISSING_DATA,
                retryable=False,
                detail="the material ledger is unavailable",
            )
        return await super().assess(ctx)


async def test_planning_still_runs_when_rm_fails(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order, organization, factory = await _seed(db_session)
    run = await _start_run(db_session, order, organization, factory)
    await db_session.commit()

    with temporary_agent("rm", FailingRMAgent):
        await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        tasks = await _tasks(session, run.id)
        by_key = {(task.recipient, task.round): task for task in tasks}
        assert by_key[("rm", 0)].status == TaskStatus.FAILED.value
        assert by_key[("rm", 0)].error_code == AgentErrorCode.MISSING_DATA.value
        assert ("planning", 0) in by_key

        planning = await _result(session, by_key[("planning", 0)])
        assert "rm" in planning.data_quality.missing
        assert planning.data_quality.complete is False

        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.status == RunStatus.AWAITING_REVIEW.value
        assert stored.degraded_reason is not None
        assert "rm:" in stored.degraded_reason


class _OutageLLM(FixtureLLMClient):
    async def complete(self, **kwargs: Any) -> Any:
        raise LLMUnavailableError("the provider is unavailable")


async def test_rm_provider_outage_degrades_to_the_deterministic_assessment(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order, organization, factory = await _seed(db_session)
    run = await _start_run(db_session, order, organization, factory)
    await db_session.commit()

    def _client(target_settings: Settings) -> Any:
        return _OutageLLM()

    monkeypatch.setattr("app.orchestration.executor.build_llm_client", _client)
    await _drain_with_backoff(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        tasks = await _tasks(session, run.id)
        by_key = {(task.recipient, task.round): task for task in tasks}
        rm0 = await _result(session, by_key[("rm", 0)])
        assert rm0.status == "DEGRADED"
        assert rm0.summary_source == "deterministic"
        assert rm0.execution_metadata.degraded_reason == (AgentErrorCode.PROVIDER_UNAVAILABLE.value)
        assert by_key[("rm", 0)].attempt == 3
        coverable = next(m for m in rm0.metrics if m.name == "coverable_units")
        assert coverable.value == demo.DEMO_EXPECTED_COVERABLE_UNITS

        planning = await _result(session, by_key[("planning", 0)])
        assert "rm" not in planning.data_quality.missing
        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.degraded_reason is not None
        assert "rm:PROVIDER_UNAVAILABLE" in stored.degraded_reason


# --------------------------------------------------------------------------
# Deadlines, cancellation, stalls
# --------------------------------------------------------------------------


async def test_deadline_passed_cancels_open_tasks_and_finalizes(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order = await make_order(db_session)
    organization = await db_session.get(Organization, order.organization_id)
    factory = await db_session.get(Factory, order.factory_id)
    assert organization is not None and factory is not None
    run = await _start_run(
        db_session,
        order,
        organization,
        factory,
        status=RunStatus.RUNNING.value,
        deadline_at=datetime.now(tz=UTC) - timedelta(seconds=1),
    )
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
    await db_session.commit()

    await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.status == RunStatus.FAILED.value
        assert stored.error_code == AgentErrorCode.DEADLINE_EXCEEDED.value
        cancelled = await session.get(AgentTask, task.id)
        assert cancelled is not None
        assert cancelled.status == TaskStatus.CANCELLED.value
        assert "run.finalized" in await _events(session, run.id)


async def test_cancelled_run_is_left_alone(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order = await make_order(db_session)
    organization = await db_session.get(Organization, order.organization_id)
    factory = await db_session.get(Factory, order.factory_id)
    assert organization is not None and factory is not None
    run = await _start_run(
        db_session, order, organization, factory, status=RunStatus.CANCELLED.value
    )
    await db_session.commit()

    await drain(session_factory, settings, transport=_transport(app))

    async with session_factory() as session:
        assert await _tasks(session, run.id) == []
        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.status == RunStatus.CANCELLED.value
        assert stored.completed_at is None


async def test_reconcile_enqueues_an_advance_for_a_stalled_run(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    order = await make_order(db_session)
    organization = await db_session.get(Organization, order.organization_id)
    factory = await db_session.get(Factory, order.factory_id)
    assert organization is not None and factory is not None
    requester = await make_requester(db_session, organization, factory)
    run, _snapshot = await make_run_with_snapshot(
        db_session, order=order, requested_by=requester, status=RunStatus.RUNNING.value
    )
    await db_session.commit()

    # Nothing is queued for this run and it has been quiet for a while.
    report = await reconcile_once(
        session_factory, settings, now=datetime.now(tz=UTC) + timedelta(minutes=1)
    )
    assert report.runs_advanced == 1

    async with session_factory() as session:
        job = await session.scalar(
            sa.select(Job).where(
                Job.job_type == ORCHESTRATOR_ADVANCE_JOB,
                Job.payload["run_id"].astext == str(run.id),
            )
        )
        assert job is not None
        assert job.status == JobStatus.READY.value
        assert job.dedupe_key is not None
        assert job.dedupe_key.startswith(f"run:{run.id}:reconcile:")


async def test_reconcile_leaves_a_busy_run_alone(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    order = await make_order(db_session)
    organization = await db_session.get(Organization, order.organization_id)
    factory = await db_session.get(Factory, order.factory_id)
    assert organization is not None and factory is not None
    requester = await make_requester(db_session, organization, factory)
    run, _snapshot = await make_run_with_snapshot(
        db_session, order=order, requested_by=requester, status=RunStatus.RUNNING.value
    )
    await enqueue(
        db_session,
        queue="orchestrator",
        job_type=ORCHESTRATOR_ADVANCE_JOB,
        payload={"run_id": str(run.id)},
        dedupe_key=f"run:{run.id}:busy",
    )
    await db_session.commit()

    report = await reconcile_once(
        session_factory, settings, now=datetime.now(tz=UTC) + timedelta(minutes=1)
    )

    assert report.runs_advanced == 0


# --------------------------------------------------------------------------
# Worker restart recovery
# --------------------------------------------------------------------------


async def test_a_crashed_worker_leaves_exactly_one_result_per_task(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    app: Any,
) -> None:
    order, organization, factory = await _seed(db_session)
    run = await _start_run(db_session, order, organization, factory)
    await db_session.commit()

    # Worker A gets as far as dispatching planning round 0, then dies holding
    # its lease on that task's execution job.
    transport = _transport(app)
    worker_a = make_worker(session_factory, settings, transport=transport, worker_id="worker-a")
    for _ in range(20):
        async with session_factory() as session:
            planning = await session.scalar(
                sa.select(AgentTask).where(
                    AgentTask.run_id == run.id, AgentTask.recipient == "planning"
                )
            )
        if planning is not None:
            break
        assert await worker_a.run_once() is True
    else:  # pragma: no cover - the dispatch always lands well before this
        raise AssertionError("planning round 0 was never dispatched")

    stolen = await claim(session_factory, queues=["agent"], worker_id="worker-a", lease_seconds=30)
    assert stolen is not None
    assert stolen.job_type == AGENT_EXECUTE_JOB
    # The lease expires without the handler ever finishing (a hard crash).
    async with session_factory() as session, session.begin():
        job = await session.get(Job, stolen.id)
        assert job is not None
        job.leased_until = datetime.now(tz=UTC) - timedelta(seconds=1)

    await drain(session_factory, settings, transport=transport, worker_id="worker-b")

    async with session_factory() as session:
        tasks = await _tasks(session, run.id)
        for task in tasks:
            count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(AgentResultRecord)
                .where(AgentResultRecord.task_id == task.id)
            )
            assert count == 1, f"{task.recipient} round {task.round} has {count} results"
        stored = await session.get(AnalysisRun, run.id)
        assert stored is not None
        assert stored.status in (
            RunStatus.AWAITING_REVIEW.value,
            RunStatus.COMPLETED.value,
            RunStatus.DEGRADED.value,
        )


# --------------------------------------------------------------------------
# The replan rule itself
# --------------------------------------------------------------------------


def _result_for(
    agent: str,
    *,
    status: str = "SUCCEEDED",
    metrics: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> AgentResult:
    stamp = "2026-09-17T00:00:00+00:00"
    return AgentResult.model_validate(
        {
            "schema_version": "1.0",
            "task_id": str(uuid.uuid4()),
            "agent": agent,
            "status": status,
            "summary": f"{agent} summary.",
            "summary_source": "deterministic",
            "findings": [],
            "metrics": metrics or [],
            "recommended_actions": actions or [],
            "evidence_refs": [],
            "warnings": [],
            "input_versions": {},
            "data_quality": {"complete": True, "missing": [], "notes": []},
            "execution_metadata": {
                "provider": "fixture",
                "model": "fixture-scripted-v1",
                "model_calls": 1,
                "tool_calls": [],
                "input_tokens": 1,
                "output_tokens": 1,
                "prompt_version": f"{agent}-v1",
                "degraded": False,
                "degraded_reason": None,
                "started_at": stamp,
                "completed_at": stamp,
            },
            "error_code": None,
        }
    )


def _allocation(allocated_units: str, *, rank: int = 0, kind: str = "ALLOCATION") -> dict[str, Any]:
    return {
        "action_id": f"act-{rank}",
        "kind": kind,
        "summary": f"Allocate {allocated_units} units.",
        "payload": {
            "option_code": "FULL_EARLIEST",
            "allocations": [],
            "allocated_units": allocated_units,
            "unscheduled_units": "0",
            "unscheduled_reason": None,
            "finish_date": None,
        },
        "evidence_ids": [],
        "rank": rank,
        "source": "deterministic",
    }


def _coverable(value: str) -> list[dict[str, Any]]:
    return [{"name": "coverable_units", "value": value, "unit": "units"}]


def test_needs_replan_fires_only_when_the_plan_outruns_the_materials() -> None:
    planning = _result_for("planning", actions=[_allocation("1000")])
    assert needs_replan(planning, _result_for("rm", metrics=_coverable("873"))) == (
        "MATERIAL_SHORTAGE_CONFLICT: selected plan allocates 1000 units but materials cover 873"
    )
    # Exactly at the limit, and below it, are not conflicts.
    assert needs_replan(planning, _result_for("rm", metrics=_coverable("1000"))) is None
    assert needs_replan(planning, _result_for("rm", metrics=_coverable("1200"))) is None


def test_needs_replan_is_none_when_a_result_is_missing_or_failed() -> None:
    planning = _result_for("planning", actions=[_allocation("1000")])
    rm = _result_for("rm", metrics=_coverable("873"))

    assert needs_replan(None, None) is None
    assert needs_replan(planning, None) is None
    assert needs_replan(None, rm) is None
    assert (
        needs_replan(_result_for("planning", status="FAILED", actions=[_allocation("1000")]), rm)
        is None
    )
    assert (
        needs_replan(planning, _result_for("rm", status="FAILED", metrics=_coverable("873")))
        is None
    )


def test_needs_replan_is_none_without_a_coverable_metric_or_an_allocation() -> None:
    planning = _result_for("planning", actions=[_allocation("1000")])
    # RM reported no coverable_units at all (e.g. it degraded before computing one).
    assert needs_replan(planning, _result_for("rm")) is None
    assert (
        needs_replan(
            planning,
            _result_for("rm", metrics=[{"name": "shortage:M01", "value": "160", "unit": "m"}]),
        )
        is None
    )
    # A null metric value is "unknown", never "zero".
    assert (
        needs_replan(
            planning,
            _result_for(
                "rm", metrics=[{"name": "coverable_units", "value": None, "unit": "units"}]
            ),
        )
        is None
    )

    rm = _result_for("rm", metrics=_coverable("873"))
    # No ALLOCATION action to compare against.
    assert needs_replan(_result_for("planning"), rm) is None
    assert (
        needs_replan(_result_for("planning", actions=[_allocation("1000", kind="RESERVATION")]), rm)
        is None
    )


def test_needs_replan_reads_the_rank_zero_allocation() -> None:
    rm = _result_for("rm", metrics=_coverable("873"))
    # The rank-1 option would conflict; only the selected (rank-0) one counts.
    planning = _result_for(
        "planning", actions=[_allocation("1000", rank=1), _allocation("800", rank=0)]
    )
    assert needs_replan(planning, rm) is None
