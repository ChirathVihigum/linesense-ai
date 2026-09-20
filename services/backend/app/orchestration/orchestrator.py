"""The run orchestrator: the only component that creates agent tasks.

``orchestrator.advance`` is delivered whenever something about a run may have
changed (it was created, a task finished, the reconciler noticed it stalled).
Each delivery does *one* thing — start the run, dispatch the next task, or
finalize — and is safe to deliver twice: the run row is locked only while
state is read and written, dispatch is idempotent on the protocol's
``idempotency_key``, and finalization is guarded by the run's own status.

Tasks never create tasks. The graph lives here and nowhere else (see
``docs/architecture/agent-protocol.md``): RM, IE and quality round 0 are
dispatched together when the run starts; planning round 0 waits for RM *and*
IE; one targeted replan may follow, then RM round 1 validates the selected
plan; quality is independent of the planning chain but must be terminal
before the run is finalized.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    AnalysisRun,
    Notification,
    Order,
    Recommendation,
    RunSnapshot,
)
from app.domain.clock import utcnow
from app.domain.vocab import (
    REPORT_EVENT_TYPE,
    ActorType,
    AuditOutcome,
    RecommendationStatus,
    Role,
    RunStatus,
    TaskStatus,
)
from app.jobs.queue import PermanentJobError, RetryableJobError
from app.jobs.worker import JobContext, finish_in_transaction
from app.orchestration.dispatch import AgentDispatchClient, DispatchError, make_envelope
from app.orchestration.events import append_event
from app.orchestration.protocol import AgentErrorCode, AgentResult, InputRef, TaskEnvelope
from app.orchestration.recommendations import create_recommendation
from app.orchestration.snapshot import SnapshotData
from app.orchestration.synthesis import build_order_report

logger = structlog.get_logger("app.orchestration")

ORCHESTRATOR_ACTOR = "orchestrator"
RUN_FINALIZED_EVENT = "run.finalized"
RUN_STARTED_EVENT = "run.started"
REPLAN_EVENT = "orchestrator.replan"
NOTIFICATION_KIND = "analysis.completed"
SUPERSEDED_REASON = "NEWER_ANALYSIS"
ANALYSIS_COMPLETED_ACTION = "analysis.completed"

RM_ASSESS = "assess_material_readiness"
RM_VALIDATE = "validate_plan_materials"
PLANNING_PROPOSE = "propose_allocation"
PLANNING_REVISE = "revise_allocation"
IE_ASSESS = "assess_line_capability"
QUALITY_ASSESS = "assess_quality_status"
# Dispatched together the moment the run starts: none of the three depends on
# another, and planning must not wait for them one after the other.
ROUND_ZERO: tuple[tuple[str, str], ...] = (
    ("rm", RM_ASSESS),
    ("ie", IE_ASSESS),
    ("quality", QUALITY_ASSESS),
)

_TERMINAL_RUN_STATUSES = (
    RunStatus.AWAITING_REVIEW.value,
    RunStatus.COMPLETED.value,
    RunStatus.DEGRADED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
)
_TERMINAL_TASK_STATUSES = (
    TaskStatus.SUCCEEDED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
)
_ACTIVE_TASK_STATUSES = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
_SUPERSEDABLE = (RecommendationStatus.PROPOSED.value, RecommendationStatus.APPROVED.value)
# A DEGRADED result still carries the agent's deterministic content.
_USABLE_RESULT_STATUSES = ("SUCCEEDED", "DEGRADED")


# --------------------------------------------------------------------------
# The replan rule
# --------------------------------------------------------------------------


def _top_allocation(result: AgentResult | None) -> Any:
    if result is None or result.status == "FAILED":
        return None
    actions = sorted(
        (action for action in result.recommended_actions if action.kind == "ALLOCATION"),
        key=lambda action: action.rank,
    )
    return actions[0] if actions else None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def needs_replan(planning_result: AgentResult | None, rm_result: AgentResult | None) -> str | None:
    """Why the selected plan must be revised, or ``None`` when it need not be.

    The one targeted replan this system performs: the plan the planning agent
    put first commits more units than the RM agent says materials cover.
    """
    if planning_result is None or rm_result is None:
        return None
    if planning_result.status == "FAILED" or rm_result.status == "FAILED":
        return None
    action = _top_allocation(planning_result)
    if action is None:
        return None
    allocated = _decimal(action.payload.get("allocated_units"))
    coverable = next(
        (metric.value for metric in rm_result.metrics if metric.name == "coverable_units"), None
    )
    if allocated is None or coverable is None or allocated <= coverable:
        return None
    return (
        f"MATERIAL_SHORTAGE_CONFLICT: selected plan allocates {allocated} units "
        f"but materials cover {coverable}"
    )


# --------------------------------------------------------------------------
# Loading run state
# --------------------------------------------------------------------------


async def _lock_run(session: AsyncSession, run_id: uuid.UUID) -> AnalysisRun | None:
    run: AnalysisRun | None = await session.scalar(
        sa.select(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return run


async def _tasks(session: AsyncSession, run: AnalysisRun) -> list[AgentTask]:
    return list(
        (
            await session.scalars(
                sa.select(AgentTask)
                .where(AgentTask.run_id == run.id)
                .order_by(AgentTask.created_at, AgentTask.id)
            )
        ).all()
    )


async def _results(session: AsyncSession, run: AnalysisRun) -> dict[uuid.UUID, AgentResult]:
    rows = (
        await session.scalars(
            sa.select(AgentResultRecord).where(AgentResultRecord.run_id == run.id)
        )
    ).all()
    return {row.task_id: AgentResult.model_validate(row.payload) for row in rows}


def _task(tasks: list[AgentTask], recipient: str, round_: int) -> AgentTask | None:
    return next(
        (task for task in tasks if task.recipient == recipient and task.round == round_), None
    )


def _terminal(task: AgentTask) -> bool:
    return task.status in _TERMINAL_TASK_STATUSES


def _envelope(
    run: AnalysisRun,
    *,
    recipient: str,
    task_type: str,
    round_: int,
    parent_task_id: uuid.UUID | None,
    input_refs: list[InputRef],
) -> TaskEnvelope:
    return make_envelope(
        run,
        recipient=recipient,
        task_type=task_type,
        round=round_,
        parent_task_id=parent_task_id,
        input_refs=input_refs,
        deadline_at=run.deadline_at,
    )


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


async def _decide(session: AsyncSession, run: AnalysisRun) -> list[TaskEnvelope]:
    """Advance ``run`` by one step, returning the tasks to dispatch (if any).

    ``run`` is locked; every write here happens in the caller's transaction,
    which commits *before* any HTTP dispatch. Only the run's start returns
    more than one envelope: the three independent round-0 tasks.
    """
    if run.status in _TERMINAL_RUN_STATUSES:
        return []
    if run.snapshot_id is None:  # pragma: no cover - runs are created with a snapshot
        raise PermanentJobError(f"run {run.id} has no snapshot to reason about")

    tasks = await _tasks(session, run)
    results = await _results(session, run)
    snapshot_ref = InputRef(type="snapshot", id=run.snapshot_id)

    if utcnow() >= run.deadline_at:
        await _finalize_deadline(session, run, tasks, results)
        return []

    if run.status == RunStatus.QUEUED.value:
        run.status = RunStatus.RUNNING.value
        run.started_at = utcnow()
        run.version += 1
        await append_event(
            session,
            run.id,
            RUN_STARTED_EVENT,
            ORCHESTRATOR_ACTOR,
            {"snapshot_id": str(run.snapshot_id)},
        )

    rm0 = _task(tasks, "rm", 0)
    ie0 = _task(tasks, "ie", 0)
    quality0 = _task(tasks, "quality", 0)
    planning0 = _task(tasks, "planning", 0)
    planning1 = _task(tasks, "planning", 1)
    rm1 = _task(tasks, "rm", 1)

    # The run's start: RM, IE and quality go out together. A delivery that
    # found only some of them created (a dispatch that failed mid-way) sends
    # the rest; dispatch is idempotent on the protocol's idempotency key.
    missing = [
        _envelope(
            run,
            recipient=recipient,
            task_type=task_type,
            round_=0,
            parent_task_id=None,
            input_refs=[snapshot_ref],
        )
        for recipient, task_type in ROUND_ZERO
        if _task(tasks, recipient, 0) is None
    ]
    if missing:
        return missing
    assert rm0 is not None and ie0 is not None and quality0 is not None  # noqa: S101

    # Planning needs both material and line capability; quality runs beside them.
    if not _terminal(rm0) or not _terminal(ie0):
        return []
    rm_refs = [InputRef(type="agent_result", id=rm0.id)] if rm0.id in results else []
    ie_refs = [InputRef(type="agent_result", id=ie0.id)] if ie0.id in results else []

    if planning0 is None:
        return [
            _envelope(
                run,
                recipient="planning",
                task_type=PLANNING_PROPOSE,
                round_=0,
                parent_task_id=None,
                input_refs=[snapshot_ref, *rm_refs, *ie_refs],
            )
        ]
    if not _terminal(planning0):
        return []

    if planning1 is None:
        reason = needs_replan(results.get(planning0.id), results.get(rm0.id))
        # ``replan_count`` is committed before the dispatch, so a dispatch that
        # has to be retried still re-dispatches the same revision task.
        if reason is not None or run.replan_count > 0:
            if run.replan_count == 0:
                run.replan_count = 1
                run.version += 1
                await append_event(
                    session, run.id, REPLAN_EVENT, ORCHESTRATOR_ACTOR, {"reason": reason}
                )
            return [
                _envelope(
                    run,
                    recipient="planning",
                    task_type=PLANNING_REVISE,
                    round_=1,
                    parent_task_id=planning0.id,
                    input_refs=[
                        *rm_refs,
                        *ie_refs,
                        InputRef(type="agent_result", id=planning0.id),
                    ],
                )
            ]

    final_planning = planning1 or planning0
    if not _terminal(final_planning):
        return []
    final_result = results.get(final_planning.id)

    if rm1 is None and _allocates_units(final_result):
        return [
            _envelope(
                run,
                recipient="rm",
                task_type=RM_VALIDATE,
                round_=1,
                parent_task_id=final_planning.id,
                input_refs=[snapshot_ref, InputRef(type="agent_result", id=final_planning.id)],
            )
        ]
    if rm1 is not None and not _terminal(rm1):
        return []
    # Quality is independent of the planning chain, but the report cannot be
    # written without it.
    if not _terminal(quality0):
        return []

    await _finalize(session, run, tasks, results)
    return []


def _allocates_units(result: AgentResult | None) -> bool:
    """Whether the selected plan commits units that RM must still validate.

    A DEGRADED plan counts: its allocation is deterministic, and
    ``create_recommendation`` will propose it, so its materials must be
    validated before a human is asked to approve it.
    """
    if result is None or result.status not in _USABLE_RESULT_STATUSES:
        return False
    action = _top_allocation(result)
    if action is None:
        return False
    allocated = _decimal(action.payload.get("allocated_units"))
    return allocated is not None and allocated > 0


# --------------------------------------------------------------------------
# Finalization
# --------------------------------------------------------------------------


def _degraded_reasons(results: dict[uuid.UUID, AgentResult], tasks: list[AgentTask]) -> list[str]:
    reasons: list[str] = []
    for task in tasks:
        result = results.get(task.id)
        if result is None or result.status == "SUCCEEDED":
            continue
        reason = (
            result.execution_metadata.degraded_reason
            or (result.error_code.value if result.error_code else None)
            or result.status
        )
        reasons.append(f"{result.agent}:{reason}")
    return reasons


async def _supersede_previous(
    session: AsyncSession, run: AnalysisRun, keep: uuid.UUID | None
) -> None:
    await session.execute(
        sa.update(Recommendation)
        .where(
            Recommendation.order_id == run.order_id,
            Recommendation.status.in_(_SUPERSEDABLE),
            Recommendation.id != (keep or uuid.UUID(int=0)),
        )
        .values(
            status=RecommendationStatus.SUPERSEDED.value,
            superseded_reason=SUPERSEDED_REASON,
            version=Recommendation.version + 1,
        )
        .execution_options(synchronize_session=False)
    )


async def _close_run(
    session: AsyncSession,
    run: AnalysisRun,
    *,
    status: str,
    degraded_reason: str | None,
    error_code: str | None,
    recommendation_id: uuid.UUID | None,
    completed_at: datetime,
) -> None:
    run.status = status
    run.degraded_reason = degraded_reason
    run.error_code = error_code
    run.completed_at = completed_at
    run.version += 1
    await append_event(
        session,
        run.id,
        RUN_FINALIZED_EVENT,
        ORCHESTRATOR_ACTOR,
        {
            "status": status,
            "degraded_reason": degraded_reason,
            "error_code": error_code,
            "recommendation_id": str(recommendation_id) if recommendation_id else None,
        },
    )
    order = await session.get(Order, run.order_id)
    external_ref = order.external_ref if order is not None else str(run.order_id)
    session.add(
        Notification(
            id=uuid.uuid4(),
            organization_id=run.organization_id,
            factory_id=run.factory_id,
            user_id=None,
            role=Role.SUPERVISOR.value,
            kind=NOTIFICATION_KIND,
            title=f"Analysis {status.lower().replace('_', ' ')} for order {external_ref}",
            body=(
                f"The analysis run for order {external_ref} finished with status {status}."
                + (
                    " A recommendation is waiting for review."
                    if recommendation_id is not None
                    else ""
                )
            ),
            link=f"/runs/{run.id}",
        )
    )
    await record_audit(
        session,
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        actor_type=ActorType.SYSTEM.value,
        actor_id=ORCHESTRATOR_ACTOR,
        action=ANALYSIS_COMPLETED_ACTION,
        target_type="analysis_run",
        target_id=str(run.id),
        outcome=AuditOutcome.SUCCESS.value,
        reason=degraded_reason,
        trace_id=run.trace_id,
        run_id=run.id,
        after={
            "status": status,
            "recommendation_id": str(recommendation_id) if recommendation_id else None,
            "error_code": error_code,
        },
    )
    await session.flush()
    logger.info(
        "orchestrator.run_finalized",
        run_id=str(run.id),
        status=status,
        recommendation_id=str(recommendation_id) if recommendation_id else None,
    )


async def _finalize(
    session: AsyncSession,
    run: AnalysisRun,
    tasks: list[AgentTask],
    results: dict[uuid.UUID, AgentResult],
) -> None:
    rm0 = _task(tasks, "rm", 0)
    final_planning = _task(tasks, "planning", 1) or _task(tasks, "planning", 0)
    rm1 = _task(tasks, "rm", 1)
    planning_result = results.get(final_planning.id) if final_planning is not None else None
    rm1_result = results.get(rm1.id) if rm1 is not None else None

    snapshot = await session.get(RunSnapshot, run.snapshot_id)
    input_versions = dict(snapshot.input_versions) if snapshot is not None else {}
    recommendation = await create_recommendation(
        session, run, input_versions, planning_result, rm1_result
    )
    if recommendation is not None:
        await _supersede_previous(session, run, recommendation.id)

    reasons = _degraded_reasons(results, tasks)
    degraded_reason = ", ".join(reasons) if reasons else None
    rm0_failed = rm0 is not None and rm0.status == TaskStatus.FAILED.value
    planning_failed = (
        final_planning is not None and final_planning.status == TaskStatus.FAILED.value
    )
    error_code: str | None = None

    if rm0_failed and planning_failed:
        status = RunStatus.FAILED.value
        error_code = _first_error_code(results, [rm0, final_planning])
    elif recommendation is not None:
        status = RunStatus.AWAITING_REVIEW.value
    elif not reasons:
        status = RunStatus.COMPLETED.value
        degraded_reason = None
    else:
        status = RunStatus.DEGRADED.value

    run.status = status
    await _append_report(session, run, tasks, results, snapshot, recommendation)
    await _close_run(
        session,
        run,
        status=status,
        degraded_reason=degraded_reason,
        error_code=error_code,
        recommendation_id=recommendation.id if recommendation is not None else None,
        completed_at=utcnow(),
    )


async def _append_report(
    session: AsyncSession,
    run: AnalysisRun,
    tasks: list[AgentTask],
    results: dict[uuid.UUID, AgentResult],
    snapshot: RunSnapshot | None,
    recommendation: Recommendation | None,
) -> None:
    """Write the run's canonical order report as a ``run.report`` event.

    ``run.status`` is already the status the run is closing with, so the
    report's ``states.analysis`` matches what the API will return.
    """
    if snapshot is None:  # pragma: no cover - runs are created with a snapshot
        logger.warning("orchestrator.report_skipped", run_id=str(run.id), reason="no snapshot")
        return
    report = build_order_report(
        run,
        SnapshotData.model_validate(snapshot.data),
        results,
        recommendation,
        tasks=tasks,
    )
    await append_event(
        session,
        run.id,
        REPORT_EVENT_TYPE,
        ORCHESTRATOR_ACTOR,
        report.model_dump(mode="json"),
    )


def _first_error_code(
    results: dict[uuid.UUID, AgentResult], tasks: list[AgentTask | None]
) -> str | None:
    for task in tasks:
        if task is None:
            continue
        result = results.get(task.id)
        if result is not None and result.error_code is not None:
            return result.error_code.value
        if task.error_code:
            return task.error_code
    return None


async def _finalize_deadline(
    session: AsyncSession,
    run: AnalysisRun,
    tasks: list[AgentTask],
    results: dict[uuid.UUID, AgentResult],
) -> None:
    """The run ran out of time: stop every open task and close it."""
    await session.execute(
        sa.update(AgentTask)
        .where(AgentTask.run_id == run.id, AgentTask.status.in_(_ACTIVE_TASK_STATUSES))
        .values(
            status=TaskStatus.CANCELLED.value,
            error_code=AgentErrorCode.DEADLINE_EXCEEDED.value,
            error_detail="the run deadline passed before this task finished",
            completed_at=sa.func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    for task in tasks:
        # The rows were just cancelled in SQL; keep the loaded copies in step so
        # the report describes the tasks as they now are.
        if task.status in _ACTIVE_TASK_STATUSES:
            task.status = TaskStatus.CANCELLED.value
    planning_succeeded = any(
        result.agent == "planning" and result.status == "SUCCEEDED" for result in results.values()
    )
    reasons = _degraded_reasons(results, tasks)
    status = RunStatus.DEGRADED.value if planning_succeeded else RunStatus.FAILED.value
    # Set before the report is built, so a cancelled task's degraded reason in
    # the report is the run's own error code (``_close_run`` writes it again).
    run.status = status
    run.error_code = AgentErrorCode.DEADLINE_EXCEEDED.value
    await _append_report(
        session, run, tasks, results, await session.get(RunSnapshot, run.snapshot_id), None
    )
    await _close_run(
        session,
        run,
        status=status,
        degraded_reason=", ".join(reasons) if reasons else AgentErrorCode.DEADLINE_EXCEEDED.value,
        error_code=AgentErrorCode.DEADLINE_EXCEEDED.value,
        recommendation_id=None,
        completed_at=utcnow(),
    )


# --------------------------------------------------------------------------
# The job handler
# --------------------------------------------------------------------------


def _dispatch_client(ctx: JobContext) -> AgentDispatchClient:
    if ctx.dispatch_client is not None:
        return ctx.dispatch_client
    return AgentDispatchClient(  # pragma: no cover - the worker always supplies one
        ctx.settings.api_internal_url, ctx.settings.service_token.get_secret_value()
    )


async def advance_run(ctx: JobContext) -> None:
    """Move ``run_id`` one step forward (job ``orchestrator.advance``)."""
    run_id = uuid.UUID(str(ctx.job.payload["run_id"]))

    async with ctx.session_factory() as session, session.begin():
        run = await _lock_run(session, run_id)
        if run is None:
            logger.warning("orchestrator.unknown_run", run_id=str(run_id))
            await finish_in_transaction(ctx, session)
            return
        envelopes = await _decide(session, run)

    for envelope in envelopes:
        try:
            receipt = await _dispatch_client(ctx).submit(envelope)
        except DispatchError as exc:
            if exc.retryable:
                raise RetryableJobError(f"dispatch failed: {exc.message}") from exc
            logger.warning(
                "orchestrator.dispatch_rejected",
                run_id=str(run_id),
                recipient=envelope.recipient,
                task_type=envelope.task_type,
                round=envelope.round,
                status_code=exc.status_code,
                reason=exc.message,
            )
            raise PermanentJobError(f"dispatch rejected: {exc.message}") from exc
        logger.info(
            "orchestrator.task_dispatched",
            run_id=str(run_id),
            task_id=str(receipt.task_id),
            recipient=envelope.recipient,
            task_type=envelope.task_type,
            round=envelope.round,
        )

    async with ctx.session_factory() as session, session.begin():
        await finish_in_transaction(ctx, session)


__all__ = ["ORCHESTRATOR_ACTOR", "advance_run", "needs_replan"]
