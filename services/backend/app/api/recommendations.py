"""Recommendation routes: list, detail, decision and transactional apply
(task-14-brief.md requirement 4).

`apply` is the only route in the codebase that owns its database session
rather than taking the `get_db_session` dependency. It needs to:

* retry the *whole* transaction (bounded, jittered) when PostgreSQL raises a
  serialization failure or a deadlock, and
* commit the transaction of a `PersistedRejection` (the expiry and staleness
  paths record a status change that must survive the 409 they raise), while
  releasing the idempotency claim so the key is not burned on an error.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from psycopg import errors as psycopg_errors
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.orders import request_trace_id as _request_trace_id
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.recommendations import (
    AppliedAllocationOut,
    AppliedOrderOut,
    AppliedReservationOut,
    ApplyRequest,
    ApplyResultOut,
    DecisionOut,
    DecisionRequest,
    DecisionResult,
    EvidenceOut,
    ProposalDiff,
    RecommendationDetailOut,
    RecommendationOrderRef,
    RecommendationSummary,
    ReservationDiffOut,
    RunRefOut,
    SlotDiffOut,
    StaleInputOut,
)
from app.api.schemas.runs import OrderRef, UserRef, llm_label
from app.auth.policy import Principal
from app.db.models import Recommendation
from app.db.session import get_db_session, get_session_factory
from app.domain.approvals import service as approvals
from app.domain.vocab import RecommendationStatus
from app.idempotency.service import release as idempotency_release

router = APIRouter(prefix="/api/v1", tags=["recommendations"])

APPLY_OPERATION = "recommendation:apply"
MAX_APPLY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 0.02
# 40001 serialization_failure, 40P01 deadlock_detected.
RETRYABLE_SQLSTATES = frozenset(
    {psycopg_errors.SerializationFailure.sqlstate, psycopg_errors.DeadlockDetected.sqlstate}
)


def _is_retryable(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) in RETRYABLE_SQLSTATES


def _user_ref(ref: approvals.UserRef) -> UserRef:
    return UserRef(id=ref.id, display_name=ref.display_name)


def _summary(row: approvals.RecommendationRow) -> RecommendationSummary:
    rec = row.recommendation
    return RecommendationSummary(
        id=rec.id,
        order=OrderRef(id=row.order.id, external_ref=row.order.external_ref),
        run_id=rec.run_id,
        kind=rec.kind,
        status=rec.status,
        generated_by=rec.generated_by,
        proposed_by_agent=rec.proposed_by_agent,
        proposer=_user_ref(row.proposer),
        rationale=rec.rationale,
        proposal_hash=rec.proposal_hash,
        status_source=row.status_source,
        expires_at=rec.expires_at,
        expired=row.expired,
        superseded_reason=rec.superseded_reason,
        applied_at=rec.applied_at,
        created_at=rec.created_at,
    )


def _detail(data: approvals.RecommendationDetail) -> RecommendationDetailOut:
    rec = data.recommendation
    return RecommendationDetailOut(
        id=rec.id,
        order=RecommendationOrderRef(
            id=data.order.id,
            external_ref=data.order.external_ref,
            production_state=data.order.production_state,
            material_state=data.order.material_state,
            quantity=data.order.quantity,
            due_date=data.order.due_date,
            version=data.order.version,
        ),
        run=RunRefOut(
            id=data.run.id,
            status=data.run.status,
            llm=llm_label(data.run.llm_provider, data.run.llm_model),
        ),
        kind=rec.kind,
        status=rec.status,
        generated_by=rec.generated_by,
        proposed_by_agent=rec.proposed_by_agent,
        proposer=_user_ref(data.proposer),
        rationale=rec.rationale,
        proposal=dict(rec.proposal),
        proposal_hash=rec.proposal_hash,
        input_versions=dict(rec.input_versions),
        status_source=data.status_source,
        decision=(
            DecisionOut(
                decision=data.decision.decision,
                decided_by=_user_ref(data.decision.decided_by),
                decided_at=data.decision.decided_at,
                reason=data.decision.reason,
            )
            if data.decision is not None
            else None
        ),
        evidence=[EvidenceOut(**vars(item)) for item in data.evidence],
        diff=ProposalDiff(
            slots=[SlotDiffOut(**vars(item)) for item in data.slots],
            reservations=[ReservationDiffOut(**vars(item)) for item in data.reservations],
        ),
        stale=data.stale,
        stale_inputs=[StaleInputOut(**vars(item)) for item in data.stale_inputs],
        expired=data.expired,
        can_decide=data.can_decide,
        decide_blocked_reason=data.decide_blocked_reason,
        can_apply=data.can_apply,
        apply_blocked_reason=data.apply_blocked_reason,
        expires_at=rec.expires_at,
        superseded_reason=rec.superseded_reason,
        applied_at=rec.applied_at,
        created_at=rec.created_at,
        version=rec.version,
    )


async def _recommendation_factory_id(session: AsyncSession, rec_id: uuid.UUID) -> uuid.UUID | None:
    result: uuid.UUID | None = await session.scalar(
        select(Recommendation.factory_id).where(Recommendation.id == rec_id)
    )
    return result


@router.get("/factories/{factory_id}/recommendations", response_model=Page[RecommendationSummary])
async def list_recommendations(
    factory_id: uuid.UUID,
    status: RecommendationStatus | None = Query(default=None),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[RecommendationSummary]:
    rows, total = await approvals.list_recommendations(
        session,
        principal,
        factory_id,
        status.value if status is not None else None,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[RecommendationSummary](
        items=[_summary(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/recommendations/{rec_id}", response_model=RecommendationDetailOut)
async def get_recommendation(
    rec_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> RecommendationDetailOut:
    return _detail(await approvals.recommendation_detail(session, principal, rec_id))


@router.post("/recommendations/{rec_id}/decision", response_model=DecisionResult)
async def decide_recommendation(
    rec_id: uuid.UUID,
    body: DecisionRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> DecisionResult:
    try:
        recommendation = await approvals.decide(
            session,
            principal,
            rec_id,
            decision=body.decision,
            reason=body.reason,
            proposal_hash=body.proposal_hash,
            trace_id=_request_trace_id(request),
        )
    except approvals.PersistedRejection:
        # The EXPIRED status change is part of the answer: keep it.
        await session.commit()
        raise
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await _recommendation_factory_id(session, rec_id),
            action=approvals.DECIDE_ACTION,
            target_type="recommendation",
            target_id=str(rec_id),
        )
        raise
    decision = await approvals.decision_ref(session, recommendation)
    if decision is None:  # pragma: no cover - decide() always writes one
        raise RuntimeError(f"recommendation {recommendation.id} was decided without an approval")
    return DecisionResult(
        id=recommendation.id,
        status=recommendation.status,
        decision=decision.decision,
        decided_by=_user_ref(decision.decided_by),
        decided_at=decision.decided_at,
        reason=decision.reason,
        version=recommendation.version,
    )


def _apply_body(result: approvals.ApplyResult) -> ApplyResultOut:
    return ApplyResultOut(
        id=result.recommendation_id,
        status=result.status,
        applied_at=result.applied_at,
        order=AppliedOrderOut(
            id=result.order.id,
            external_ref=result.order.external_ref,
            production_state=result.order.production_state,
            material_state=result.order.material_state,
            version=result.order.version,
        ),
        allocations=[AppliedAllocationOut(**vars(row)) for row in result.allocations],
        reservations=[AppliedReservationOut(**vars(row)) for row in result.reservations],
        released_allocations=result.released_allocations,
        released_reservations=result.released_reservations,
    )


async def _apply_once(
    session: AsyncSession,
    request: Request,
    principal: Principal,
    rec_id: uuid.UUID,
    body: ApplyRequest,
    idempotency_key: str,
) -> JSONResponse:
    """One attempt, inside ``session``'s transaction (the caller commits)."""
    early = await idempotent_start(
        session,
        principal,
        operation=APPLY_OPERATION,
        key=idempotency_key,
        request_payload={"recommendation_id": str(rec_id), **body.model_dump()},
    )
    if early is not None:
        return early
    try:
        result = await approvals.apply(
            session,
            principal,
            rec_id,
            proposal_hash=body.proposal_hash,
            trace_id=_request_trace_id(request),
        )
    except approvals.PersistedRejection:
        # The status change is committed; the key must not be, or a retry
        # would replay a response that was never stored.
        await idempotency_release(
            session,
            organization_id=principal.organization_id,
            actor_id=str(principal.user_id),
            operation=APPLY_OPERATION,
            key=idempotency_key,
        )
        raise
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await _recommendation_factory_id(session, rec_id),
            action=approvals.APPLY_ACTION,
            target_type="recommendation",
            target_id=str(rec_id),
        )
        raise
    return await idempotent_finish(
        session,
        principal,
        operation=APPLY_OPERATION,
        key=idempotency_key,
        status_code=200,
        body=_apply_body(result),
    )


@router.post("/recommendations/{rec_id}/apply", response_model=ApplyResultOut)
async def apply_recommendation(
    rec_id: uuid.UUID,
    body: ApplyRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
) -> Any:
    session_factory = get_session_factory(request.app.state.settings.database_url)
    for attempt in range(MAX_APPLY_ATTEMPTS):
        async with session_factory() as session:
            try:
                response = await _apply_once(
                    session, request, principal, rec_id, body, idempotency_key
                )
                # Inside the `try`: COMMIT itself can raise the serialization
                # failure or deadlock this loop exists to retry.
                await session.commit()
            except approvals.PersistedRejection:
                await session.commit()
                raise
            except DBAPIError as exc:
                await session.rollback()
                if not _is_retryable(exc) or attempt == MAX_APPLY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(
                    RETRY_BASE_SECONDS * (attempt + 1) * (1 + random.random())  # noqa: S311
                )
                continue
            except Exception:
                await session.rollback()
                raise
            return response
    raise RuntimeError("the bounded apply retry loop ended without a result")
