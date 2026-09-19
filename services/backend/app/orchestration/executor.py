"""The ``agent.execute`` job handler: run one agent task, once, fenced.

The agent itself runs *outside* any transaction (it calls a provider over the
network and may take tens of seconds); only the outcome is written, in a
single transaction that also completes the job under its lease token, so a
worker that lost its lease writes nothing at all.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, AgentExecutionError, BaseAgent
from app.agents.registry import AGENTS
from app.auth.policy import PERMISSIONS
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    AnalysisRun,
    Membership,
    RoleAssignment,
    RunSnapshot,
    User,
)
from app.domain.clock import utcnow
from app.domain.vocab import RunStatus, TaskStatus
from app.jobs.queue import RetryableJobError, enqueue
from app.jobs.worker import JobContext, finish_in_transaction
from app.llm import build_llm_client
from app.orchestration.events import append_event
from app.orchestration.protocol import (
    AgentErrorCode,
    AgentResult,
    DataQuality,
    ExecutionMetadata,
    Recipient,
    TaskEnvelope,
)
from app.orchestration.snapshot import SnapshotData
from app.orchestration.validation import validate_result

logger = structlog.get_logger("app.orchestration")

AGENT_EXECUTE_JOB = "agent.execute"
ORCHESTRATOR_ADVANCE_JOB = "orchestrator.advance"
RUN_PERMISSION = "analysis:run"
PROVIDER_UNAVAILABLE_WARNING = "AI explanation unavailable (provider unavailable)"
_ACTIVE_TASK_STATUSES = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
_SUCCESSFUL_RESULT_STATUSES = ("SUCCEEDED", "DEGRADED")


class _TaskFailure(Exception):
    """A non-retryable failure of one task (stored as a FAILED result)."""

    def __init__(self, code: AgentErrorCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


async def _load(
    session: AsyncSession, task_id: uuid.UUID
) -> tuple[AgentTask, AnalysisRun, RunSnapshot]:
    task = await session.get(AgentTask, task_id)
    if task is None:
        raise _TaskFailure(AgentErrorCode.MISSING_DATA, f"agent task {task_id} does not exist")
    run = await session.get(AnalysisRun, task.run_id)
    if run is None:
        raise _TaskFailure(AgentErrorCode.MISSING_DATA, f"run {task.run_id} does not exist")
    snapshot = (
        await session.get(RunSnapshot, run.snapshot_id) if run.snapshot_id is not None else None
    )
    if snapshot is None:
        raise _TaskFailure(AgentErrorCode.MISSING_DATA, f"run {run.id} has no snapshot")
    return task, run, snapshot


async def _requester_roles(session: AsyncSession, run: AnalysisRun) -> frozenset[str]:
    """Roles the run's requester still holds for the run's factory (empty if none)."""
    user = await session.get(User, run.requested_by)
    if user is None or not user.is_active:
        return frozenset()
    rows = (
        await session.execute(
            sa.select(RoleAssignment.role)
            .join(Membership, Membership.id == RoleAssignment.membership_id)
            .where(
                Membership.user_id == run.requested_by,
                Membership.organization_id == run.organization_id,
                Membership.is_active.is_(True),
                sa.or_(
                    RoleAssignment.factory_id == run.factory_id,
                    RoleAssignment.factory_id.is_(None),
                ),
            )
        )
    ).all()
    return frozenset(role for (role,) in rows)


async def _dependency_results(
    session: AsyncSession, task: AgentTask, envelope: TaskEnvelope
) -> dict[str, AgentResult]:
    """Results named by the envelope's ``agent_result`` input refs, keyed by agent.

    A dependency produced by the same agent in an earlier round is keyed
    ``"<agent>_r<round>"`` (e.g. ``planning_r0`` seen by planning round 1).
    """
    dependency_ids = [ref.id for ref in envelope.input_refs if ref.type == "agent_result"]
    if not dependency_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(AgentTask, AgentResultRecord)
            .join(AgentResultRecord, AgentResultRecord.task_id == AgentTask.id)
            .where(AgentTask.id.in_(dependency_ids), AgentTask.run_id == task.run_id)
        )
    ).all()
    results: dict[str, AgentResult] = {}
    for dependency, record in rows:
        key = (
            f"{dependency.recipient}_r{dependency.round}"
            if dependency.recipient == task.recipient
            else dependency.recipient
        )
        results[key] = AgentResult.model_validate(record.payload)
    return results


# --------------------------------------------------------------------------
# Writing the outcome
# --------------------------------------------------------------------------


def _event_payload(result: AgentResult) -> dict[str, Any]:
    return {
        "task_id": str(result.task_id),
        "status": result.status,
        "summary": result.summary,
        "summary_source": result.summary_source,
        "finding_codes": [finding.code for finding in result.findings],
        "action_ids": [action.action_id for action in result.recommended_actions],
        "error_code": result.error_code.value if result.error_code else None,
    }


async def _store_outcome(
    session: AsyncSession,
    *,
    task: AgentTask,
    run: AnalysisRun,
    snapshot: RunSnapshot,
    result: AgentResult,
) -> AgentResult:
    """Validate, persist (once), advance the run. Runs in the caller's transaction."""
    problems = await validate_result(session, run, snapshot, result, task=task)
    if problems:
        logger.warning(
            "agent.result_rejected",
            task_id=str(task.id),
            run_id=str(run.id),
            agent=task.recipient,
            problems=problems[:10],
        )
        result = _failed_result(
            task,
            snapshot,
            code=AgentErrorCode.INVALID_AGENT_OUTPUT,
            message="; ".join(problems)[:2000],
            template=result,
        )
    succeeded = result.status in _SUCCESSFUL_RESULT_STATUSES
    inserted = await session.scalar(
        insert(AgentResultRecord)
        .values(
            id=uuid.uuid4(),
            task_id=task.id,
            run_id=run.id,
            schema_version=result.schema_version,
            status=result.status,
            payload=result.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=[AgentResultRecord.task_id])
        .returning(AgentResultRecord.id)
    )
    if inserted is None:
        # Another delivery of the same task already stored its result; the
        # run was advanced by that delivery too.
        logger.info("agent.result_duplicate", task_id=str(task.id), run_id=str(run.id))
        return result
    await session.execute(
        sa.update(AgentTask)
        .where(AgentTask.id == task.id, AgentTask.status.in_(_ACTIVE_TASK_STATUSES))
        .values(
            status=TaskStatus.SUCCEEDED.value if succeeded else TaskStatus.FAILED.value,
            error_code=result.error_code.value if result.error_code else None,
            error_detail=None if succeeded else result.summary[:2000],
            completed_at=sa.func.now(),
        )
    )
    await append_event(
        session, run.id, "task.completed", f"agent:{task.recipient}", _event_payload(result)
    )
    await enqueue(
        session,
        queue="orchestrator",
        job_type=ORCHESTRATOR_ADVANCE_JOB,
        payload={"run_id": str(run.id)},
        dedupe_key=f"run:{run.id}:after:{task.id}",
    )
    return result


def _failed_result(
    task: AgentTask,
    snapshot: RunSnapshot,
    *,
    code: AgentErrorCode,
    message: str,
    template: AgentResult | None = None,
) -> AgentResult:
    """A minimal, always-valid FAILED result for ``task``."""
    now = utcnow()
    metadata = (
        template.execution_metadata.model_copy(update={"degraded": False, "degraded_reason": None})
        if template is not None
        else None
    )
    return AgentResult(
        schema_version="1.0",
        task_id=task.id,
        agent=cast("Recipient", task.recipient),
        status="FAILED",
        summary=message,
        summary_source="deterministic",
        findings=[],
        metrics=[],
        recommended_actions=[],
        evidence_refs=[],
        warnings=[],
        input_versions=dict(snapshot.input_versions),
        data_quality=DataQuality(complete=False, missing=[task.recipient], notes=[message]),
        execution_metadata=metadata
        or ExecutionMetadata(
            provider="none",
            model="none",
            model_calls=0,
            tool_calls=[],
            input_tokens=0,
            output_tokens=0,
            prompt_version="n/a",
            degraded=False,
            degraded_reason=None,
            started_at=now,
            completed_at=now,
        ),
        error_code=code,
    )


# --------------------------------------------------------------------------
# The job handler
# --------------------------------------------------------------------------


async def execute_agent_task(ctx: JobContext) -> None:
    task_id = uuid.UUID(str(ctx.job.payload["task_id"]))
    async with ctx.session_factory() as session:
        try:
            task, run, snapshot = await _load(session, task_id)
        except _TaskFailure as exc:
            logger.warning("agent.task_unrunnable", task_id=str(task_id), reason=exc.detail)
            await _finish_only(ctx)
            return
        if run.status == RunStatus.CANCELLED.value or task.status not in _ACTIVE_TASK_STATUSES:
            logger.info(
                "agent.task_skipped",
                task_id=str(task.id),
                task_status=task.status,
                run_status=run.status,
            )
            await _finish_only(ctx)
            return

    async with ctx.session_factory() as session, session.begin():
        await session.execute(
            sa.update(AgentTask)
            .where(AgentTask.id == task_id, AgentTask.status.in_(_ACTIVE_TASK_STATUSES))
            .values(
                status=TaskStatus.RUNNING.value,
                attempt=ctx.job.attempt,
                error_code=None,
                error_detail=None,
            )
        )

    result: AgentResult | None = None
    prepared: tuple[BaseAgent, AgentContext] | None = None
    async with ctx.session_factory() as session:
        task, run, snapshot = await _load(session, task_id)
        try:
            prepared = await _prepare(session, ctx, task, run, snapshot)
        except _TaskFailure as exc:
            result = _failed_result(task, snapshot, code=exc.code, message=exc.detail)

    if prepared is not None:
        agent, agent_ctx = prepared
        try:
            result = await agent.run(agent_ctx)
        except AgentExecutionError as exc:
            if exc.retryable:
                await _record_retryable(ctx, task_id, exc)
                raise RetryableJobError(f"{exc.code.value}: {exc.detail}") from exc
            result = agent.failed_result(
                agent_ctx, code=exc.code, message=exc.detail or exc.code.value
            )

    if result is None:  # pragma: no cover - one of the branches above always sets it
        raise RuntimeError(f"agent task {task_id} produced no result")
    async with ctx.session_factory() as session, session.begin():
        task, run, snapshot = await _load(session, task_id)
        await _store_outcome(session, task=task, run=run, snapshot=snapshot, result=result)
        await finish_in_transaction(ctx, session)


async def _finish_only(ctx: JobContext) -> None:
    """Complete the job without writing anything else (fenced)."""
    async with ctx.session_factory() as session, session.begin():
        await finish_in_transaction(ctx, session)


async def _prepare(
    session: AsyncSession,
    ctx: JobContext,
    task: AgentTask,
    run: AnalysisRun,
    snapshot: RunSnapshot,
) -> tuple[BaseAgent, AgentContext]:
    roles = await _requester_roles(session, run)
    if not roles & PERMISSIONS[RUN_PERMISSION]:
        raise _TaskFailure(
            AgentErrorCode.POLICY_DENIED,
            "the requesting user no longer has analysis:run on this factory",
        )
    agent_class = AGENTS.get(task.recipient)
    if agent_class is None:
        raise _TaskFailure(
            AgentErrorCode.MISSING_DATA, f"no agent is registered for {task.recipient!r}"
        )
    envelope = TaskEnvelope.model_validate(task.envelope)
    agent_ctx = AgentContext(
        run_id=run.id,
        task_id=task.id,
        round=task.round,
        task_type=task.task_type,
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        order_id=run.order_id,
        requested_by=run.requested_by,
        snapshot=SnapshotData.model_validate(snapshot.data),
        input_versions=dict(snapshot.input_versions),
        dependency_results=await _dependency_results(session, task, envelope),
        session_factory=ctx.session_factory,
        llm=build_llm_client(ctx.settings),
        settings=ctx.settings,
        deadline_at=task.deadline_at,
        requester_roles=roles,
    )
    return agent_class(), agent_ctx


async def _record_retryable(ctx: JobContext, task_id: uuid.UUID, exc: AgentExecutionError) -> None:
    """Remember the last error on the task so the exhaustion hook can use it."""
    async with ctx.session_factory() as session, session.begin():
        await session.execute(
            sa.update(AgentTask)
            .where(AgentTask.id == task_id)
            .values(error_code=exc.code.value, error_detail=exc.detail[:2000] or exc.code.value)
        )


# --------------------------------------------------------------------------
# Exhaustion
# --------------------------------------------------------------------------


async def on_agent_task_exhausted(ctx: JobContext, error: str) -> None:
    """Last-chance outcome once every attempt of ``agent.execute`` has failed.

    A provider outage degrades to the agent's deterministic assessment (no
    model call); anything else fails the task with the last error code. Both
    paths record a result, the ``task.completed`` event and an advance job, so
    the run never stalls on an exhausted task.
    """
    task_id = uuid.UUID(str(ctx.job.payload["task_id"]))
    async with ctx.session_factory() as session:
        try:
            task, run, snapshot = await _load(session, task_id)
        except _TaskFailure as exc:
            logger.warning("agent.exhausted_unrunnable", task_id=str(task_id), reason=exc.detail)
            return
        if task.status not in _ACTIVE_TASK_STATUSES:
            return
        last_code = _last_error_code(task, error)
        result: AgentResult | None = None
        if last_code is AgentErrorCode.PROVIDER_UNAVAILABLE:
            try:
                agent, agent_ctx = await _prepare(session, ctx, task, run, snapshot)
            except _TaskFailure as exc:
                result = _failed_result(task, snapshot, code=exc.code, message=exc.detail)
            else:
                try:
                    result = await agent.deterministic_result(
                        agent_ctx,
                        degraded_reason=AgentErrorCode.PROVIDER_UNAVAILABLE.value,
                        warning=PROVIDER_UNAVAILABLE_WARNING,
                    )
                except AgentExecutionError as exc:
                    result = _failed_result(
                        task, snapshot, code=exc.code, message=exc.detail or exc.code.value
                    )
        if result is None:
            result = _failed_result(
                task,
                snapshot,
                code=last_code or AgentErrorCode.MISSING_DATA,
                message=f"agent task failed after {task.max_attempts} attempts: {error}"[:2000],
            )

    async with ctx.session_factory() as session, session.begin():
        task, run, snapshot = await _load(session, task_id)
        if task.status not in _ACTIVE_TASK_STATUSES:
            return
        await _store_outcome(session, task=task, run=run, snapshot=snapshot, result=result)


def _last_error_code(task: AgentTask, error: str) -> AgentErrorCode | None:
    """The code of the attempt that exhausted the job (the task's own error
    code is cleared at the start of every attempt, so it is never stale)."""
    if task.error_code is not None:
        try:
            return AgentErrorCode(task.error_code)
        except ValueError:
            return None
    return next((code for code in AgentErrorCode if code.value in error), None)
