"""Industrial engineering routes: cycle observations, outlier marking,
operator aliases, and line/style bottleneck analysis (backend-contracts.md
sections 4-5; task-9-brief.md).

Reads need `ie:read` (all roles); writes need `ie:write` (IE engineer) and
an `Idempotency-Key`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.ie import (
    LineBalanceOut,
    LineStyleAnalysisOut,
    ObservationCreate,
    ObservationOut,
    OperationAnalysisOut,
    OperatorAliasOut,
    OutlierMarkRequest,
)
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import CycleObservation, Factory
from app.db.session import get_db_session
from app.domain.ie import service as ie_service

router = APIRouter(prefix="/api/v1", tags=["ie"])


def _observation_out(observation: CycleObservation) -> ObservationOut:
    return ObservationOut(
        id=observation.id,
        line_id=observation.line_id,
        style_id=observation.style_id,
        operation_id=observation.operation_id,
        operator_alias_id=observation.operator_alias_id,
        observed_seconds=observation.observed_seconds,
        observed_at=observation.observed_at,
        is_outlier=observation.is_outlier,
        outlier_approved_by=observation.outlier_approved_by,
        recorded_by=observation.recorded_by,
        created_at=observation.created_at,
    )


def _analysis_out(analysis: ie_service.LineStyleAnalysis) -> LineStyleAnalysisOut:
    return LineStyleAnalysisOut(
        line_id=analysis.line_id,
        style_id=analysis.style_id,
        operations=[
            OperationAnalysisOut(
                operation_id=op.operation_id,
                code=op.code,
                name=op.name,
                sam_minutes=op.sam_minutes,
                sample_count=op.sample_count,
                representative_seconds=op.representative_seconds,
                parallel_operators=op.parallel_operators,
                effective_seconds=op.effective_seconds,
                insufficient_samples=op.insufficient_samples,
            )
            for op in analysis.operations
        ],
        balance=(
            LineBalanceOut(
                bottleneck_index=analysis.balance.bottleneck_index,
                bottleneck_effective_seconds=analysis.balance.bottleneck_effective_seconds,
                units_per_hour=analysis.balance.units_per_hour,
                balance_index_percent=analysis.balance.balance_index_percent,
            )
            if analysis.balance is not None
            else None
        ),
        observed_units_per_hour=analysis.observed_units_per_hour,
        sam_units_per_hour=analysis.sam_units_per_hour,
        assumptions=analysis.assumptions,
        limitations=analysis.limitations,
        data_versions=analysis.data_versions,
    )


async def _run_command(
    request: Request,
    session: AsyncSession,
    principal: Principal,
    *,
    operation: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
    status_code: int,
    audit_action: str,
    audit_target_type: str,
    audit_target_id: str,
    audit_factory_id: Callable[[], Awaitable[uuid.UUID | None]],
    command: Callable[[], Awaitable[Any]],
) -> Any:
    """Idempotency + denied-audit wrapper shared by every IE write."""
    early = await idempotent_start(
        session,
        principal,
        operation=operation,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early
    try:
        body = await command()
    except AppError as exc:
        if exc.status_code == 403:
            await audit_denial_from_error(
                request,
                principal,
                exc,
                factory_id=await audit_factory_id(),
                action=audit_action,
                target_type=audit_target_type,
                target_id=audit_target_id,
            )
        raise
    return await idempotent_finish(
        session,
        principal,
        operation=operation,
        key=idempotency_key,
        status_code=status_code,
        body=body,
    )


def _fixed(factory_id: uuid.UUID) -> Callable[[], Awaitable[uuid.UUID | None]]:
    async def resolve() -> uuid.UUID | None:
        return factory_id

    return resolve


def _factory_of(
    session: AsyncSession, observation_id: uuid.UUID
) -> Callable[[], Awaitable[uuid.UUID | None]]:
    async def resolve() -> uuid.UUID | None:
        result: uuid.UUID | None = await session.scalar(
            select(CycleObservation.factory_id).where(CycleObservation.id == observation_id)
        )
        return result

    return resolve


@router.get(
    "/factories/{factory_id}/ie/lines/{line_id}/styles/{style_id}/analysis",
    response_model=LineStyleAnalysisOut,
)
async def line_style_analysis(
    factory_id: uuid.UUID,
    line_id: uuid.UUID,
    style_id: uuid.UUID,
    window_days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> LineStyleAnalysisOut:
    factory = await load_scoped(session, Factory, factory_id, principal, "ie:read")
    analysis = await ie_service.line_style_analysis(
        session, factory.id, line_id, style_id, window_days=window_days
    )
    return _analysis_out(analysis)


@router.post(
    "/factories/{factory_id}/ie/observations", response_model=ObservationOut, status_code=201
)
async def record_observation(
    factory_id: uuid.UUID,
    body: ObservationCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> ObservationOut:
        observation = await ie_service.record_observation(
            session,
            principal,
            factory_id,
            ie_service.NewObservationInput(
                line_id=body.line_id,
                style_id=body.style_id,
                operation_id=body.operation_id,
                operator_alias_code=body.operator_alias_code,
                observed_seconds=body.observed_seconds,
                observed_at=body.observed_at,
            ),
        )
        return _observation_out(observation)

    return await _run_command(
        request,
        session,
        principal,
        operation="ie:observation_record",
        idempotency_key=idempotency_key,
        request_payload={"factory_id": str(factory_id), **body.model_dump()},
        status_code=201,
        audit_action="ie.observation.record",
        audit_target_type="cycle_observation",
        audit_target_id=str(factory_id),
        audit_factory_id=_fixed(factory_id),
        command=command,
    )


@router.post("/ie/observations/{observation_id}/outlier", response_model=ObservationOut)
async def mark_outlier(
    observation_id: uuid.UUID,
    body: OutlierMarkRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> ObservationOut:
        observation = await ie_service.mark_outlier(session, principal, observation_id, body.reason)
        return _observation_out(observation)

    return await _run_command(
        request,
        session,
        principal,
        operation="ie:observation_mark_outlier",
        idempotency_key=idempotency_key,
        request_payload={"observation_id": str(observation_id), **body.model_dump()},
        status_code=200,
        audit_action="ie.observation.mark_outlier",
        audit_target_type="cycle_observation",
        audit_target_id=str(observation_id),
        audit_factory_id=_factory_of(session, observation_id),
        command=command,
    )


@router.get("/factories/{factory_id}/ie/operator-aliases", response_model=Page[OperatorAliasOut])
async def list_operator_aliases(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[OperatorAliasOut]:
    factory = await load_scoped(session, Factory, factory_id, principal, "ie:read")
    rows, total = await ie_service.list_operator_aliases(
        session, factory.id, limit=page.limit, offset=page.offset
    )
    return Page[OperatorAliasOut](
        items=[
            OperatorAliasOut(
                id=row.id, alias_code=row.alias_code, line_id=row.line_id, is_active=row.is_active
            )
            for row in rows
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
