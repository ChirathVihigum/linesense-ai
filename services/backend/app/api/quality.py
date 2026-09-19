"""Quality routes: inspections, holds, separated releases, shipment
eligibility, and defect trends (backend-contracts.md sections 4-5;
task-9-brief.md).

Reads need `quality:read`/`order:read` (all roles); `quality:inspect`,
`quality:hold` and `quality:release` are quality-manager only. Writes need
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
from app.api.schemas.quality import (
    DEMO_POLICY_LABEL,
    DefectCodeTrendOut,
    DefectOut,
    DefectTrendOut,
    HoldCreate,
    InspectionCreate,
    OrderQualityOut,
    PolicyOut,
    QualityHoldOut,
    QualityInspectionOut,
    ReleaseCreate,
    ReleaseOut,
    ShipmentOut,
)
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import DefectObservation, Factory, Inspection, Order, QualityHold, QualityRelease
from app.db.session import get_db_session
from app.domain.quality import service as quality_service
from app.domain.quality.calc import shipment_eligibility

router = APIRouter(prefix="/api/v1", tags=["quality"])


def _defect_out(defect: DefectObservation) -> DefectOut:
    return DefectOut(
        defect_code=defect.defect_code,
        severity=defect.severity,
        count=defect.count,
        operation_id=defect.operation_id,
    )


def _inspection_out(
    inspection: Inspection, defects: list[DefectObservation]
) -> QualityInspectionOut:
    return QualityInspectionOut(
        id=inspection.id,
        order_id=inspection.order_id,
        line_id=inspection.line_id,
        inspection_type=inspection.inspection_type,
        inspected_units=inspection.inspected_units,
        defective_units=inspection.defective_units,
        policy_version_id=inspection.policy_version_id,
        result=inspection.result,
        inspected_by=inspection.inspected_by,
        inspected_at=inspection.inspected_at,
        defects=[_defect_out(defect) for defect in defects],
    )


def _hold_out(hold: QualityHold) -> QualityHoldOut:
    return QualityHoldOut(
        id=hold.id,
        order_id=hold.order_id,
        inspection_id=hold.inspection_id,
        reason=hold.reason,
        status=hold.status,
        created_by=hold.created_by,
        created_at=hold.created_at,
        released_by=hold.released_by,
        released_at=hold.released_at,
        release_id=hold.release_id,
    )


def _release_out(release: QualityRelease) -> ReleaseOut:
    return ReleaseOut(
        id=release.id,
        order_id=release.order_id,
        inspection_id=release.inspection_id,
        policy_version_id=release.policy_version_id,
        released_by=release.released_by,
        released_at=release.released_at,
        notes=release.notes,
    )


def _policy_out(policy: Any) -> PolicyOut | None:
    if policy is None:
        return None
    return PolicyOut(
        id=policy.id,
        code=policy.code,
        version_no=policy.version_no,
        is_demo=policy.is_demo,
        status=policy.status,
        rules=policy.rules,
        label=DEMO_POLICY_LABEL if policy.is_demo else None,
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
    """Idempotency + denied-audit wrapper shared by every quality write."""
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


def _order_factory_id(
    session: AsyncSession, order_id: uuid.UUID
) -> Callable[[], Awaitable[uuid.UUID | None]]:
    async def resolve() -> uuid.UUID | None:
        result: uuid.UUID | None = await session.scalar(
            select(Order.factory_id).where(Order.id == order_id)
        )
        return result

    return resolve


async def _order_quality_out(session: AsyncSession, order: Order) -> OrderQualityOut:
    inspections = await quality_service.list_inspections(session, order.id)
    defects = await quality_service.defects_by_inspection(
        session, [inspection.id for inspection in inspections]
    )
    holds = await quality_service.order_holds(session, order.id)
    releases = await quality_service.list_releases(session, order.id)
    facts = await quality_service.shipment_facts(session, order)
    eligibility = shipment_eligibility(facts)
    policy = await quality_service.active_policy(session, order.organization_id)

    return OrderQualityOut(
        order_id=order.id,
        order_version=order.version,
        quality_state=order.quality_state,
        inspections=[
            _inspection_out(inspection, defects.get(inspection.id, []))
            for inspection in inspections
        ],
        holds=[_hold_out(hold) for hold in holds],
        releases=[_release_out(release) for release in releases],
        shipment=ShipmentOut(eligible=eligibility.eligible, reasons=list(eligibility.reasons)),
        policy=_policy_out(policy),
    )


@router.get("/orders/{order_id}/quality", response_model=OrderQualityOut)
async def order_quality(
    order_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> OrderQualityOut:
    order = await load_scoped(session, Order, order_id, principal, "quality:read")
    return await _order_quality_out(session, order)


@router.post("/orders/{order_id}/inspections", response_model=OrderQualityOut, status_code=201)
async def create_inspection(
    order_id: uuid.UUID,
    body: InspectionCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> OrderQualityOut:
        await quality_service.record_inspection(
            session,
            principal,
            order_id,
            quality_service.NewInspectionInput(
                inspection_type=body.inspection_type,
                inspected_units=body.inspected_units,
                defective_units=body.defective_units,
                defects=[
                    quality_service.DefectInput(
                        defect_code=defect.defect_code,
                        severity=defect.severity,
                        count=defect.count,
                        operation_id=defect.operation_id,
                    )
                    for defect in body.defects
                ],
                line_id=body.line_id,
            ),
        )
        order = await session.get(Order, order_id)
        if order is None:
            raise RuntimeError("inspected order vanished within its own transaction")
        return await _order_quality_out(session, order)

    return await _run_command(
        request,
        session,
        principal,
        operation="quality:inspection_record",
        idempotency_key=idempotency_key,
        request_payload={"order_id": str(order_id), **body.model_dump()},
        status_code=201,
        audit_action="quality.inspection.record",
        audit_target_type="inspection",
        audit_target_id=str(order_id),
        audit_factory_id=_order_factory_id(session, order_id),
        command=command,
    )


@router.post("/orders/{order_id}/holds", response_model=OrderQualityOut, status_code=201)
async def create_hold(
    order_id: uuid.UUID,
    body: HoldCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> OrderQualityOut:
        await quality_service.place_hold(session, principal, order_id, body.reason)
        order = await session.get(Order, order_id)
        if order is None:
            raise RuntimeError("held order vanished within its own transaction")
        return await _order_quality_out(session, order)

    return await _run_command(
        request,
        session,
        principal,
        operation="quality:hold_place",
        idempotency_key=idempotency_key,
        request_payload={"order_id": str(order_id), **body.model_dump()},
        status_code=201,
        audit_action="quality.hold.place",
        audit_target_type="quality_hold",
        audit_target_id=str(order_id),
        audit_factory_id=_order_factory_id(session, order_id),
        command=command,
    )


@router.post("/orders/{order_id}/quality-release", response_model=OrderQualityOut)
async def release_order(
    order_id: uuid.UUID,
    body: ReleaseCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> OrderQualityOut:
        await quality_service.release_order(
            session,
            principal,
            order_id,
            inspection_id=body.inspection_id,
            expected_order_version=body.expected_order_version,
            notes=body.notes,
        )
        order = await session.get(Order, order_id)
        if order is None:
            raise RuntimeError("released order vanished within its own transaction")
        return await _order_quality_out(session, order)

    return await _run_command(
        request,
        session,
        principal,
        operation="quality:release",
        idempotency_key=idempotency_key,
        request_payload={"order_id": str(order_id), **body.model_dump()},
        status_code=200,
        audit_action="quality.release",
        audit_target_type="quality_release",
        audit_target_id=str(order_id),
        audit_factory_id=_order_factory_id(session, order_id),
        command=command,
    )


@router.get("/factories/{factory_id}/quality/holds", response_model=Page[QualityHoldOut])
async def list_factory_holds(
    factory_id: uuid.UUID,
    status: str | None = Query(default=None),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[QualityHoldOut]:
    factory = await load_scoped(session, Factory, factory_id, principal, "quality:read")
    rows, total = await quality_service.list_holds(
        session, factory.id, status=status, limit=page.limit, offset=page.offset
    )
    return Page[QualityHoldOut](
        items=[_hold_out(row) for row in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/factories/{factory_id}/quality/trends", response_model=DefectTrendOut)
async def factory_defect_trends(
    factory_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> DefectTrendOut:
    factory = await load_scoped(session, Factory, factory_id, principal, "quality:read")
    trend = await quality_service.defect_trends(session, factory.id, days=days)
    return DefectTrendOut(
        factory_id=trend.factory_id,
        window_days=trend.window_days,
        inspected_units=trend.inspected_units,
        defective_units=trend.defective_units,
        defective_rate=trend.defective_rate,
        defects_per_hundred_units=trend.defects_per_hundred_units,
        by_defect_code=[
            DefectCodeTrendOut(defect_code=row.defect_code, severity=row.severity, count=row.count)
            for row in trend.by_defect_code
        ],
    )


@router.get("/factories/{factory_id}/quality/policies", response_model=Page[PolicyOut])
async def list_quality_policies(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[PolicyOut]:
    factory = await load_scoped(session, Factory, factory_id, principal, "quality:read")
    rows, total = await quality_service.list_policies(
        session, factory.organization_id, limit=page.limit, offset=page.offset
    )
    items = [_policy_out(row) for row in rows]
    return Page[PolicyOut](
        items=[item for item in items if item is not None],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
