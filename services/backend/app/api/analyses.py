"""Requesting an analysis run for an order (``POST /orders/{id}/analyses``).

Creating a run is a command: it validates the order's state and version,
builds the immutable input snapshot, and enqueues the orchestrator — all in
one transaction, so a run always has the snapshot its agents will read.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.orders import request_trace_id as _request_trace_id
from app.api.schemas.runs import AnalysisRequest, RunAccepted
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import AnalysisRun, Order, RunSnapshot
from app.db.session import get_db_session
from app.domain.clock import utcnow
from app.domain.vocab import ActorType, AuditOutcome, ProductionState, RunStatus
from app.jobs.queue import enqueue
from app.llm import build_llm_client
from app.orchestration.events import append_event
from app.orchestration.executor import ORCHESTRATOR_ADVANCE_JOB
from app.orchestration.snapshot import build_snapshot
from app.settings import Settings

router = APIRouter(prefix="/api/v1", tags=["analyses"])

ANALYSIS_PERMISSION = "analysis:run"
ANALYSIS_OPERATION = "analysis:run"
ACTIVE_RUN_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
MAX_ACTIVE_RUNS_PER_FACTORY = 5
RATE_LIMIT_RETRY_AFTER_SECONDS = 30
BLOCKED_ORDER_STATES = (ProductionState.CANCELLED.value, ProductionState.DISPATCHED.value)


async def lock_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    """Re-select the order ``FOR UPDATE`` so concurrent requests serialize."""
    return (
        await session.scalars(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()


def llm_identity(settings: Settings) -> tuple[str, str]:
    client = build_llm_client(settings)
    return ("disabled", "disabled") if client is None else (client.provider, client.model)


async def start_run(
    session: AsyncSession,
    principal: Principal,
    order: Order,
    *,
    settings: Settings,
    trace_id: str | None,
    idempotency_key: str,
) -> AnalysisRun:
    """Create a QUEUED run with its snapshot and enqueue the orchestrator.

    ``order`` must already be locked (:func:`lock_order`) and scope-checked.
    """
    if order.production_state in BLOCKED_ORDER_STATES:
        raise AppError(
            409,
            "INVALID_TRANSITION",
            f"An order in state {order.production_state} cannot be analysed.",
        )
    active = await session.scalar(
        select(AnalysisRun.id)
        .where(AnalysisRun.order_id == order.id, AnalysisRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(AnalysisRun.created_at)
        .limit(1)
    )
    if active is not None:
        raise AppError(409, "CONFLICT", f"Analysis run {active} is already in progress.")
    factory_active = await session.scalar(
        select(sa.func.count())
        .select_from(AnalysisRun)
        .where(
            AnalysisRun.factory_id == order.factory_id,
            AnalysisRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    if int(factory_active or 0) >= MAX_ACTIVE_RUNS_PER_FACTORY:
        raise AppError(
            429,
            "RATE_LIMITED",
            "Too many analysis runs are active in this factory; try again shortly.",
            retry_after_seconds=RATE_LIMIT_RETRY_AFTER_SECONDS,
        )

    provider, model = llm_identity(settings)
    run = AnalysisRun(
        id=uuid.uuid4(),
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        status=RunStatus.QUEUED.value,
        requested_by=principal.user_id,
        idempotency_key=idempotency_key,
        llm_provider=provider,
        llm_model=model,
        trace_id=trace_id or str(uuid.uuid4()),
        deadline_at=utcnow() + timedelta(seconds=settings.run_deadline_seconds),
    )
    session.add(run)
    await session.flush()

    data, input_versions = await build_snapshot(session, order)
    snapshot = RunSnapshot(
        id=uuid.uuid4(),
        run_id=run.id,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        order_id=order.id,
        input_versions=input_versions,
        data=data.model_dump(mode="json"),
    )
    session.add(snapshot)
    await session.flush()
    run.snapshot_id = snapshot.id
    await session.flush()

    await append_event(
        session,
        run.id,
        "run.created",
        f"user:{principal.user_id}",
        {
            "order_id": str(order.id),
            "order_version": order.version,
            "snapshot_id": str(snapshot.id),
            "llm_provider": provider,
            "llm_model": model,
        },
    )
    await enqueue(
        session,
        queue="orchestrator",
        job_type=ORCHESTRATOR_ADVANCE_JOB,
        payload={"run_id": str(run.id)},
        dedupe_key=f"run:{run.id}:start",
    )
    await record_audit(
        session,
        organization_id=order.organization_id,
        factory_id=order.factory_id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="analysis.requested",
        target_type="analysis_run",
        target_id=str(run.id),
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=trace_id,
        run_id=run.id,
        after={"order_id": str(order.id), "order_version": order.version, "provider": provider},
    )
    return run


@router.post("/orders/{order_id}/analyses", response_model=RunAccepted, status_code=202)
async def request_analysis(
    order_id: uuid.UUID,
    body: AnalysisRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    request_payload = {"order_id": str(order_id), **body.model_dump()}
    early = await idempotent_start(
        session,
        principal,
        operation=ANALYSIS_OPERATION,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early

    try:
        await load_scoped(session, Order, order_id, principal, ANALYSIS_PERMISSION)
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await session.scalar(select(Order.factory_id).where(Order.id == order_id)),
            action="analysis.requested",
            target_type="order",
            target_id=str(order_id),
        )
        raise
    order = await lock_order(session, order_id)
    if order.version != body.expected_order_version:
        raise AppError(
            409,
            "STALE_INPUT",
            f"The order has changed (version {order.version}); reload and try again.",
        )
    run = await start_run(
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
        operation=ANALYSIS_OPERATION,
        key=idempotency_key,
        status_code=202,
        body=RunAccepted(run_id=run.id, status=run.status),
    )
