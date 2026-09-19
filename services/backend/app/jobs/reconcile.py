"""Periodic reconciliation of time-based state.

- ``idempotency_keys`` expired for more than an hour are deleted.
- ``recommendations`` still ``PROPOSED``/``APPROVED`` past ``expires_at``
  become ``EXPIRED`` with a ``SYSTEM`` audit event per row.
- ``analysis_runs`` still ``QUEUED``/``RUNNING`` with nothing left to run them
  (no runnable orchestrator or agent job), or past their deadline, get a fresh
  ``orchestrator.advance`` so a run can never stall silently.

Every step is idempotent, so several workers may reconcile concurrently:
recommendation rows are locked with ``SKIP LOCKED`` (a row being decided
right now is picked up by a later round) and only rows actually changed are
audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_audit
from app.db.models import AgentTask, AnalysisRun, IdempotencyKey, Job, Recommendation
from app.domain.clock import utcnow
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    JobStatus,
    RecommendationStatus,
    RunStatus,
    TaskStatus,
)
from app.jobs.queue import enqueue
from app.settings import Settings

RECONCILER_ACTOR_ID = "reconciler"
RECOMMENDATION_BATCH_SIZE = 500
PURGE_BATCH_SIZE = 500
PURGE_GRACE = timedelta(hours=1)
_EXPIRABLE = (RecommendationStatus.PROPOSED.value, RecommendationStatus.APPROVED.value)
_ACTIVE_RUN_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
_RUNNABLE_JOB_STATUSES = (JobStatus.READY.value, JobStatus.LEASED.value)
_ACTIVE_TASK_STATUSES = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
RUN_STALL_GRACE = timedelta(seconds=30)
RUN_BATCH_SIZE = 100


@dataclass(frozen=True)
class ReconcileReport:
    idempotency_keys_expired: int
    recommendations_expired: int
    runs_advanced: int = 0


def _cutoff(now: datetime | None) -> sa.ColumnElement[datetime]:
    if now is None:
        return sa.func.now()
    return sa.literal(now, sa.TIMESTAMP(timezone=True))


async def purge_expired_idempotency_keys(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete idempotency keys expired for more than ``PURGE_GRACE`` (caller's transaction).

    The grace period absorbs clock skew between API hosts (which stamp
    ``expires_at`` with their own clock) and the database. Rows locked by an
    in-flight ``idempotency.begin`` are skipped and purged by a later round.
    """
    doomed = (
        sa.select(IdempotencyKey.id)
        .where(IdempotencyKey.expires_at < _cutoff(now) - PURGE_GRACE)
        .limit(PURGE_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    deleted = await session.scalars(
        sa.delete(IdempotencyKey)
        .where(IdempotencyKey.id.in_(doomed))
        .returning(IdempotencyKey.id)
        .execution_options(synchronize_session=False)
    )
    return len(deleted.all())


async def expire_recommendations(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Mark overdue PROPOSED/APPROVED recommendations EXPIRED and audit each one."""
    rows = (
        await session.execute(
            sa.select(
                Recommendation.id,
                Recommendation.organization_id,
                Recommendation.factory_id,
                Recommendation.run_id,
                Recommendation.status,
                Recommendation.version,
            )
            .where(
                Recommendation.status.in_(_EXPIRABLE),
                Recommendation.expires_at <= _cutoff(now),
            )
            .order_by(Recommendation.id)
            .limit(RECOMMENDATION_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
    ).all()
    if not rows:
        return 0

    await session.execute(
        sa.update(Recommendation)
        .where(Recommendation.id.in_([row.id for row in rows]))
        .values(status=RecommendationStatus.EXPIRED.value, version=Recommendation.version + 1)
        .execution_options(synchronize_session=False)
    )
    for row in rows:
        await record_audit(
            session,
            organization_id=row.organization_id,
            factory_id=row.factory_id,
            actor_type=ActorType.SYSTEM.value,
            actor_id=RECONCILER_ACTOR_ID,
            action="recommendation.expire",
            target_type="recommendation",
            target_id=str(row.id),
            outcome=AuditOutcome.SUCCESS.value,
            reason="expires_at passed",
            run_id=row.run_id,
            before={"status": row.status, "version": row.version},
            after={"status": RecommendationStatus.EXPIRED.value, "version": row.version + 1},
        )
    return len(rows)


async def advance_stalled_runs(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Enqueue ``orchestrator.advance`` for every run that stopped moving.

    A run is stalled when it is ``QUEUED``/``RUNNING``, nothing has happened to
    it for :data:`RUN_STALL_GRACE`, and no ``orchestrator.advance`` for the run
    or ``agent.execute`` for one of its tasks is still runnable — the exact
    state a worker crash leaves behind. A run past its deadline is always
    advanced, so the orchestrator can finalize it.

    The dedupe key is per minute, so repeated rounds inside the same minute
    enqueue at most one job per run.
    """
    from app.orchestration.executor import ORCHESTRATOR_ADVANCE_JOB

    moment = now or utcnow()
    cutoff = moment - RUN_STALL_GRACE
    runs = (
        await session.execute(
            sa.select(AnalysisRun.id, AnalysisRun.deadline_at)
            .where(AnalysisRun.status.in_(_ACTIVE_RUN_STATUSES))
            .order_by(AnalysisRun.created_at)
            .limit(RUN_BATCH_SIZE)
        )
    ).all()
    if not runs:
        return 0

    quiet_since = sa.func.greatest(
        AnalysisRun.created_at,
        sa.func.coalesce(AnalysisRun.started_at, AnalysisRun.created_at),
        sa.func.coalesce(
            sa.select(
                sa.func.max(
                    sa.func.greatest(
                        AgentTask.created_at,
                        sa.func.coalesce(AgentTask.completed_at, AgentTask.created_at),
                    )
                )
            )
            .where(AgentTask.run_id == AnalysisRun.id)
            .scalar_subquery(),
            AnalysisRun.created_at,
        ),
    )
    quiet_ids = set(
        (
            await session.scalars(
                sa.select(AnalysisRun.id).where(
                    AnalysisRun.id.in_([row.id for row in runs]), quiet_since <= cutoff
                )
            )
        ).all()
    )
    busy_ids = await _runs_with_runnable_jobs(session, [row.id for row in runs])

    enqueued = 0
    minute = moment.strftime("%Y%m%d%H%M")
    for row in runs:
        overdue = row.deadline_at <= moment
        if not overdue and (row.id not in quiet_ids or row.id in busy_ids):
            continue
        job_id = await enqueue(
            session,
            queue="orchestrator",
            job_type=ORCHESTRATOR_ADVANCE_JOB,
            payload={"run_id": str(row.id)},
            dedupe_key=f"run:{row.id}:reconcile:{minute}",
        )
        if job_id is not None:
            enqueued += 1
    return enqueued


async def _runs_with_runnable_jobs(
    session: AsyncSession, run_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Ids among ``run_ids`` that still have a READY/LEASED job working for them."""
    from app.orchestration.executor import AGENT_EXECUTE_JOB, ORCHESTRATOR_ADVANCE_JOB

    if not run_ids:
        return set()
    wanted = {str(run_id): run_id for run_id in run_ids}
    busy: set[uuid.UUID] = set()
    for job_type, payload_key in (
        (ORCHESTRATOR_ADVANCE_JOB, "run_id"),
        (AGENT_EXECUTE_JOB, "task_id"),
    ):
        values = (
            await session.scalars(
                sa.select(Job.payload[payload_key].astext).where(
                    Job.status.in_(_RUNNABLE_JOB_STATUSES), Job.job_type == job_type
                )
            )
        ).all()
        if payload_key == "run_id":
            busy.update(wanted[value] for value in values if value in wanted)
            continue
        task_ids = [uuid.UUID(value) for value in values if _is_uuid(value)]
        if not task_ids:
            continue
        busy.update(
            (
                await session.scalars(
                    sa.select(AgentTask.run_id).where(
                        AgentTask.id.in_(task_ids), AgentTask.run_id.in_(run_ids)
                    )
                )
            ).all()
        )
    # A task the executor is still running counts as progress too.
    busy.update(
        (
            await session.scalars(
                sa.select(AgentTask.run_id).where(
                    AgentTask.run_id.in_(run_ids),
                    AgentTask.status == TaskStatus.RUNNING.value,
                )
            )
        ).all()
    )
    return busy


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


async def reconcile_in_transaction(
    session: AsyncSession, *, now: datetime | None = None
) -> ReconcileReport:
    """Run every reconciliation step inside the caller's transaction."""
    return ReconcileReport(
        idempotency_keys_expired=await purge_expired_idempotency_keys(session, now=now),
        recommendations_expired=await expire_recommendations(session, now=now),
        runs_advanced=await advance_stalled_runs(session, now=now),
    )


async def reconcile_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> ReconcileReport:
    """One reconciliation round in its own transaction.

    ``now`` overrides the database clock (tests); by default ``now()`` is used.
    ``settings`` is part of the stable signature for later reconciliation
    steps (Task 13) that depend on configuration.
    """
    async with session_factory() as session, session.begin():
        return await reconcile_in_transaction(session, now=now)
