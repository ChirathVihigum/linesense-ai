"""Analysis run routes: detail, event stream, per-order list, cancel and retry."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analyses import ANALYSIS_PERMISSION, lock_order, start_run
from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.orders import request_trace_id as _request_trace_id
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.runs import (
    LlmLabel,
    OrderRef,
    RecommendationOut,
    RunAccepted,
    RunDetail,
    RunEventOut,
    RunSummary,
    TaskOut,
    UserRef,
    llm_label,
)
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    AgentResultRecord,
    AgentTask,
    AnalysisRun,
    Job,
    Order,
    Recommendation,
    RunEvent,
    User,
)
from app.db.session import get_db_session
from app.domain.vocab import (
    ActorType,
    AuditOutcome,
    JobStatus,
    RecommendationStatus,
    RunStatus,
    TaskStatus,
)
from app.orchestration.events import append_event
from app.orchestration.executor import AGENT_EXECUTE_JOB
from app.orchestration.protocol import AgentResult
from app.orchestration.synthesis import REPORT_EVENT_TYPE

router = APIRouter(prefix="/api/v1", tags=["runs"])

READ_PERMISSION = "analysis:read"
MAX_EVENTS = 500
CANCELLABLE_STATUSES = (
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.AWAITING_REVIEW.value,
)
RETRYABLE_STATUSES = (
    RunStatus.FAILED.value,
    RunStatus.DEGRADED.value,
    RunStatus.CANCELLED.value,
)
ACTIVE_TASK_STATUSES = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
SUPERSEDABLE_STATUSES = (
    RecommendationStatus.DRAFT.value,
    RecommendationStatus.PROPOSED.value,
    RecommendationStatus.APPROVED.value,
)
RUN_CANCELLED_REASON = "RUN_CANCELLED"


async def _summary_parts(
    session: AsyncSession, run: AnalysisRun
) -> tuple[OrderRef, UserRef, LlmLabel]:
    order = await session.get(Order, run.order_id)
    user = await session.get(User, run.requested_by)
    if order is None or user is None:
        raise RuntimeError(f"run {run.id} references a missing order or user")
    return (
        OrderRef(id=order.id, external_ref=order.external_ref),
        UserRef(id=user.id, display_name=user.display_name),
        llm_label(run.llm_provider, run.llm_model),
    )


def _summary(run: AnalysisRun, order: OrderRef, user: UserRef, llm: LlmLabel) -> RunSummary:
    return RunSummary(
        id=run.id,
        order=order,
        status=run.status,
        requested_by=user,
        llm=llm,
        degraded_reason=run.degraded_reason,
        error_code=run.error_code,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


async def _load_run(
    session: AsyncSession, run_id: uuid.UUID, principal: Principal, permission: str
) -> AnalysisRun:
    return await load_scoped(session, AnalysisRun, run_id, principal, permission)


async def _lock_run(session: AsyncSession, run_id: uuid.UUID) -> AnalysisRun:
    return (
        await session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> RunDetail:
    run = await _load_run(session, run_id, principal, READ_PERMISSION)
    order, user, llm = await _summary_parts(session, run)
    tasks = (
        await session.scalars(
            select(AgentTask)
            .where(AgentTask.run_id == run.id)
            .order_by(AgentTask.created_at, AgentTask.id)
        )
    ).all()
    results = (
        await session.scalars(
            select(AgentResultRecord)
            .where(AgentResultRecord.run_id == run.id)
            .order_by(AgentResultRecord.created_at, AgentResultRecord.id)
        )
    ).all()
    recommendations = (
        await session.scalars(
            select(Recommendation)
            .where(Recommendation.run_id == run.id)
            .order_by(Recommendation.created_at.desc())
        )
    ).all()
    report = await session.scalar(
        select(RunEvent.payload)
        .where(RunEvent.run_id == run.id, RunEvent.event_type == REPORT_EVENT_TYPE)
        .order_by(RunEvent.id.desc())
        .limit(1)
    )
    return RunDetail(
        **_summary(run, order, user, llm).model_dump(),
        model_calls_used=run.model_calls_used,
        model_calls_limit=run.model_calls_limit,
        tokens_used=run.tokens_used,
        replan_count=run.replan_count,
        started_at=run.started_at,
        deadline_at=run.deadline_at,
        tasks=[
            TaskOut(
                id=task.id,
                recipient=task.recipient,
                task_type=task.task_type,
                round=task.round,
                status=task.status,
                attempt=task.attempt,
                error_code=task.error_code,
                created_at=task.created_at,
                completed_at=task.completed_at,
                parent_task_id=task.parent_task_id,
            )
            for task in tasks
        ],
        results=[AgentResult.model_validate(record.payload) for record in results],
        recommendations=[
            RecommendationOut(
                id=recommendation.id,
                kind=recommendation.kind,
                status=recommendation.status,
                generated_by=recommendation.generated_by,
                proposed_by_agent=recommendation.proposed_by_agent,
                rationale=recommendation.rationale,
                proposal_hash=recommendation.proposal_hash,
                expires_at=recommendation.expires_at,
                superseded_reason=recommendation.superseded_reason,
                created_at=recommendation.created_at,
            )
            for recommendation in recommendations
        ],
        report=report,
    )


@router.get("/runs/{run_id}/events", response_model=list[RunEventOut])
async def list_run_events(
    run_id: uuid.UUID,
    after_id: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[RunEventOut]:
    run = await _load_run(session, run_id, principal, READ_PERMISSION)
    events = (
        await session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run.id, RunEvent.id > after_id)
            .order_by(RunEvent.id)
            .limit(MAX_EVENTS)
        )
    ).all()
    return [
        RunEventOut(
            id=event.id,
            event_type=event.event_type,
            actor=event.actor,
            payload=event.payload,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.get("/orders/{order_id}/runs", response_model=Page[RunSummary])
async def list_order_runs(
    order_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[RunSummary]:
    await load_scoped(session, Order, order_id, principal, READ_PERMISSION)
    base = select(AnalysisRun).where(AnalysisRun.order_id == order_id)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    runs = (
        await session.scalars(
            base.order_by(AnalysisRun.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    ).all()
    items: list[RunSummary] = []
    for run in runs:
        order, user, llm = await _summary_parts(session, run)
        items.append(_summary(run, order, user, llm))
    return Page[RunSummary](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )


@router.post("/runs/{run_id}/cancel", response_model=RunAccepted)
async def cancel_run(
    run_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    early = await idempotent_start(
        session,
        principal,
        operation="run:cancel",
        key=idempotency_key,
        request_payload={"run_id": str(run_id)},
    )
    if early is not None:
        return early
    try:
        run = await _load_run(session, run_id, principal, ANALYSIS_PERMISSION)
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await session.scalar(
                select(AnalysisRun.factory_id).where(AnalysisRun.id == run_id)
            ),
            action="analysis.cancelled",
            target_type="analysis_run",
            target_id=str(run_id),
        )
        raise
    run = await _lock_run(session, run.id)
    if run.status not in CANCELLABLE_STATUSES:
        raise AppError(409, "INVALID_TRANSITION", f"A {run.status} run cannot be cancelled.")

    previous_status = run.status
    task_ids = list(
        (await session.scalars(select(AgentTask.id).where(AgentTask.run_id == run.id))).all()
    )
    await session.execute(
        sa.update(AgentTask)
        .where(AgentTask.run_id == run.id, AgentTask.status.in_(ACTIVE_TASK_STATUSES))
        .values(status=TaskStatus.CANCELLED.value, completed_at=sa.func.now())
    )
    if task_ids:
        await session.execute(
            sa.update(Job)
            .where(
                Job.status == JobStatus.READY.value,
                Job.job_type == AGENT_EXECUTE_JOB,
                Job.payload["task_id"].astext.in_([str(task_id) for task_id in task_ids]),
            )
            .values(status=JobStatus.CANCELLED.value, completed_at=sa.func.now())
        )
    await session.execute(
        sa.update(Recommendation)
        .where(Recommendation.run_id == run.id, Recommendation.status.in_(SUPERSEDABLE_STATUSES))
        .values(
            status=RecommendationStatus.SUPERSEDED.value,
            superseded_reason=RUN_CANCELLED_REASON,
            version=Recommendation.version + 1,
        )
    )
    run.status = RunStatus.CANCELLED.value
    run.completed_at = sa.func.now()
    run.version += 1
    await session.flush()
    await append_event(
        session,
        run.id,
        "run.cancelled",
        f"user:{principal.user_id}",
        {"previous_status": previous_status, "cancelled_tasks": [str(t) for t in task_ids]},
    )
    await record_audit(
        session,
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="analysis.cancelled",
        target_type="analysis_run",
        target_id=str(run.id),
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=_request_trace_id(request),
        run_id=run.id,
        before={"status": previous_status},
        after={"status": RunStatus.CANCELLED.value},
    )
    return await idempotent_finish(
        session,
        principal,
        operation="run:cancel",
        key=idempotency_key,
        status_code=200,
        body=RunAccepted(run_id=run.id, status=RunStatus.CANCELLED.value),
    )


@router.post("/runs/{run_id}/retry", response_model=RunAccepted, status_code=202)
async def retry_run(
    run_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    early = await idempotent_start(
        session,
        principal,
        operation="run:retry",
        key=idempotency_key,
        request_payload={"run_id": str(run_id)},
    )
    if early is not None:
        return early
    try:
        run = await _load_run(session, run_id, principal, ANALYSIS_PERMISSION)
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await session.scalar(
                select(AnalysisRun.factory_id).where(AnalysisRun.id == run_id)
            ),
            action="analysis.requested",
            target_type="analysis_run",
            target_id=str(run_id),
        )
        raise
    if run.status not in RETRYABLE_STATUSES:
        raise AppError(409, "INVALID_TRANSITION", f"A {run.status} run cannot be retried.")
    order = await lock_order(session, run.order_id)
    new_run = await start_run(
        session,
        principal,
        order,
        settings=request.app.state.settings,
        trace_id=_request_trace_id(request),
        idempotency_key=idempotency_key,
    )
    return await idempotent_finish(
        session,
        principal,
        operation="run:retry",
        key=idempotency_key,
        status_code=202,
        body=RunAccepted(run_id=new_run.id, status=new_run.status),
    )
