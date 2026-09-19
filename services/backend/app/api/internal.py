"""Internal dispatch API (``/internal/v1``, backend-contracts.md section 6).

Service-to-service only: a bearer service token, never a user session, and
never part of the public OpenAPI document (the protocol is documented in
``contracts/`` and ``docs/architecture/agent-protocol.md``). Every envelope is
re-validated against the run it claims to belong to before a task is created.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.audit.service import audit_denied
from app.db.models import AgentResultRecord, AgentTask, AnalysisRun
from app.db.session import get_db_session, get_session_factory
from app.domain.vocab import ActorType, RunStatus, TaskStatus
from app.jobs.queue import enqueue
from app.orchestration.events import append_event
from app.orchestration.executor import AGENT_EXECUTE_JOB
from app.orchestration.protocol import (
    ALLOWED_TASK_TYPES,
    DispatchReceipt,
    TaskEnvelope,
    idempotency_key_for,
)

logger = structlog.get_logger("app.orchestration")

router = APIRouter(prefix="/internal/v1", tags=["internal"], include_in_schema=False)

DISPATCH_ACTION = "agent_task.dispatch"
SERVICE_ACTOR = "orchestrator"
_DISPATCHABLE_RUN_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)


async def require_service_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """Constant-time bearer check against ``LS_SERVICE_TOKEN``.

    Both sides are compared as bytes: ``secrets.compare_digest`` raises
    ``TypeError`` for a ``str`` holding non-ASCII characters, and a header is
    attacker-controlled (Starlette decodes it as latin-1, so any byte can
    appear there).
    """
    expected = request.app.state.settings.service_token.get_secret_value().encode("utf-8")
    presented = b""
    if authorization is not None:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":  # noqa: S105 - a scheme name, not a secret
            presented = value.strip().encode("utf-8", errors="surrogateescape")
    if not secrets.compare_digest(presented, expected) or not presented:
        raise AppError(401, "UNAUTHENTICATED", "A valid service token is required.")


async def _deny(
    request: Request,
    run: AnalysisRun,
    *,
    reason: str,
    status_code: int,
    code: str,
    field_errors: list[dict[str, str]] | None = None,
) -> AppError:
    """Audit a rejected dispatch (SERVICE actor, ``DENIED``) and build the error."""
    await audit_denied(
        get_session_factory(request.app.state.settings.database_url),
        organization_id=run.organization_id,
        factory_id=run.factory_id,
        actor_type=ActorType.SERVICE.value,
        actor_id=SERVICE_ACTOR,
        action=DISPATCH_ACTION,
        target_type="analysis_run",
        target_id=str(run.id),
        reason=reason,
        trace_id=getattr(request.state, "trace_id", None),
        run_id=run.id,
    )
    return AppError(status_code, code, reason, field_errors=field_errors)


async def _existing_task(session: AsyncSession, idempotency_key: str) -> AgentTask | None:
    task: AgentTask | None = await session.scalar(
        sa.select(AgentTask).where(AgentTask.idempotency_key == idempotency_key)
    )
    return task


@router.post("/agent-tasks", dependencies=[Depends(require_service_token)])
async def dispatch_agent_task(
    envelope: TaskEnvelope,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    run = await session.get(AnalysisRun, envelope.run_id)
    if run is None:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Unknown run.",
            field_errors=[{"field": "run_id", "message": "No such analysis run."}],
        )

    mismatches = [
        field
        for field, value in (
            ("organization_id", run.organization_id),
            ("factory_id", run.factory_id),
            ("order_id", run.order_id),
            ("snapshot_id", run.snapshot_id),
        )
        if getattr(envelope, field) != value
    ]
    if mismatches:
        raise await _deny(
            request,
            run,
            reason=f"Envelope does not match the run: {', '.join(mismatches)}.",
            status_code=422,
            code="VALIDATION_ERROR",
            field_errors=[
                {"field": field, "message": "Does not match the run."} for field in mismatches
            ],
        )

    expected_key = idempotency_key_for(
        run.id, envelope.recipient, envelope.snapshot_id, envelope.round
    )
    if envelope.idempotency_key != expected_key:
        raise await _deny(
            request,
            run,
            reason="idempotency_key does not follow run:recipient:snapshot:round.",
            status_code=422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "idempotency_key", "message": "Unexpected key."}],
        )

    existing = await _existing_task(session, envelope.idempotency_key)
    if existing is not None:
        return JSONResponse(
            status_code=200,
            content=DispatchReceipt(
                task_id=existing.id, run_id=existing.run_id, status=existing.status
            ).model_dump(mode="json"),
        )

    if run.status not in _DISPATCHABLE_RUN_STATUSES:
        raise await _deny(
            request,
            run,
            reason=f"Run is {run.status}; no further tasks can be dispatched.",
            status_code=409,
            code="CONFLICT",
        )
    if envelope.task_type not in ALLOWED_TASK_TYPES[envelope.recipient]:
        raise await _deny(
            request,
            run,
            reason=f"task_type {envelope.task_type} is not allowed for {envelope.recipient}.",
            status_code=422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "task_type", "message": "Not allowed for this recipient."}],
        )
    if envelope.deadline_at > run.deadline_at:
        raise await _deny(
            request,
            run,
            reason="deadline_at is later than the run deadline.",
            status_code=422,
            code="VALIDATION_ERROR",
            field_errors=[{"field": "deadline_at", "message": "Later than the run deadline."}],
        )

    referenced_task_ids = [ref.id for ref in envelope.input_refs if ref.type == "agent_result"]
    if envelope.parent_task_id is not None:
        referenced_task_ids.append(envelope.parent_task_id)
    if referenced_task_ids:
        known = set(
            (
                await session.scalars(
                    sa.select(AgentTask.id).where(
                        AgentTask.id.in_(referenced_task_ids), AgentTask.run_id == run.id
                    )
                )
            ).all()
        )
        unknown = [str(task_id) for task_id in referenced_task_ids if task_id not in known]
        if unknown:
            raise await _deny(
                request,
                run,
                reason=f"Referenced tasks do not belong to this run: {', '.join(unknown)}.",
                status_code=422,
                code="VALIDATION_ERROR",
                field_errors=[{"field": "input_refs", "message": "Unknown task reference."}],
            )

    task_id = uuid.uuid4()
    inserted = await session.scalar(
        insert(AgentTask)
        .values(
            id=task_id,
            run_id=run.id,
            organization_id=run.organization_id,
            factory_id=run.factory_id,
            parent_task_id=envelope.parent_task_id,
            message_id=envelope.message_id,
            sender=envelope.sender,
            recipient=envelope.recipient,
            task_type=envelope.task_type,
            round=envelope.round,
            idempotency_key=envelope.idempotency_key,
            status=TaskStatus.PENDING.value,
            envelope=envelope.model_dump(mode="json"),
            deadline_at=envelope.deadline_at,
        )
        .on_conflict_do_nothing()
        .returning(AgentTask.id)
    )
    if inserted is None:
        # A concurrent dispatch won the race (same idempotency key), or the
        # message_id was reused with different content.
        existing = await _existing_task(session, envelope.idempotency_key)
        if existing is None:
            raise AppError(409, "CONFLICT", "message_id has already been used.")
        return JSONResponse(
            status_code=200,
            content=DispatchReceipt(
                task_id=existing.id, run_id=existing.run_id, status=existing.status
            ).model_dump(mode="json"),
        )

    await enqueue(
        session,
        queue="agent",
        job_type=AGENT_EXECUTE_JOB,
        payload={"task_id": str(task_id)},
        dedupe_key=f"task:{task_id}",
        max_attempts=3,
    )
    await append_event(
        session,
        run.id,
        "task.dispatched",
        SERVICE_ACTOR,
        {
            "task_id": str(task_id),
            "recipient": envelope.recipient,
            "task_type": envelope.task_type,
            "round": envelope.round,
            "message": envelope.model_dump(mode="json"),
        },
    )
    logger.info(
        "agent.task_dispatched",
        task_id=str(task_id),
        run_id=str(run.id),
        recipient=envelope.recipient,
        task_type=envelope.task_type,
        round=envelope.round,
    )
    return JSONResponse(
        status_code=202,
        content=DispatchReceipt(
            task_id=task_id, run_id=run.id, status=TaskStatus.PENDING.value
        ).model_dump(mode="json"),
    )


@router.get("/agent-tasks/{task_id}", dependencies=[Depends(require_service_token)])
async def get_agent_task(
    task_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    task = await session.get(AgentTask, task_id)
    if task is None:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    result = await session.scalar(
        sa.select(AgentResultRecord).where(AgentResultRecord.task_id == task.id)
    )
    return {
        "task": {
            "id": str(task.id),
            "run_id": str(task.run_id),
            "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
            "recipient": task.recipient,
            "task_type": task.task_type,
            "round": task.round,
            "status": task.status,
            "attempt": task.attempt,
            "max_attempts": task.max_attempts,
            "error_code": task.error_code,
            "error_detail": task.error_detail,
            "deadline_at": task.deadline_at.isoformat(),
            "created_at": task.created_at.isoformat(),
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "envelope": task.envelope,
        },
        "result": result.payload if result is not None else None,
    }
