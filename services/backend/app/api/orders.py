"""Order routes: list, create, detail, lifecycle transitions and production
progress (backend-contracts.md sections 3-5; task-7-brief.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.orders import (
    AllocationOut,
    BomLineOut,
    BomOut,
    CustomerRef,
    FactoryRef,
    HoldOut,
    InspectionOut,
    LatestRunOut,
    OperationOut,
    OrderCreate,
    OrderDetail,
    OrderHistoryEventOut,
    OrderProgressRequest,
    OrderSummary,
    OrderTransitionRequest,
    ReservationOut,
    ShipmentStatus,
    StyleRef,
)
from app.audit.service import audit_denied
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import AuditEvent, Customer, Order, Style, User
from app.db.session import get_db_session, get_session_factory
from app.domain.orders import service as orders_service
from app.domain.quality.calc import ShipmentEligibility
from app.domain.vocab import ActorType, MaterialState, ProductionState, QualityState
from app.idempotency.service import StoredResponse
from app.idempotency.service import begin as idempotency_begin
from app.idempotency.service import finish as idempotency_finish

router = APIRouter(prefix="/api/v1", tags=["orders"])


def _to_summary(
    order: Order, customer: Customer, style: Style, shipment: ShipmentEligibility
) -> OrderSummary:
    return OrderSummary(
        id=order.id,
        external_ref=order.external_ref,
        customer=CustomerRef(id=customer.id, code=customer.code, name=customer.name),
        style=StyleRef(id=style.id, code=style.code, name=style.name),
        quantity=order.quantity,
        produced_units=order.produced_units,
        packed_units=order.packed_units,
        due_date=order.due_date,
        priority=order.priority,
        production_state=order.production_state,
        material_state=order.material_state,
        quality_state=order.quality_state,
        shipment=ShipmentStatus(eligible=shipment.eligible, reasons=list(shipment.reasons)),
        version=order.version,
        updated_at=order.updated_at,
    )


def _to_detail(data: orders_service.OrderDetailData) -> OrderDetail:
    summary = _to_summary(data.order, data.customer, data.style, data.shipment)
    return OrderDetail(
        **summary.model_dump(),
        factory=FactoryRef(id=data.factory.id, code=data.factory.code, name=data.factory.name),
        bom=BomOut(
            version_no=data.bom_version.version_no,
            lines=[
                BomLineOut(
                    material_code=material.code,
                    material_name=material.name,
                    quantity_per_unit=line.quantity_per_unit,
                    unit=line.unit,
                    wastage_fraction=line.wastage_fraction,
                )
                for line, material in data.bom_lines
            ],
        ),
        operations=[
            OperationOut(
                sequence=op.sequence, code=op.code, name=op.name, sam_minutes=op.sam_minutes
            )
            for op in data.operations
        ],
        allocations=[
            AllocationOut(
                id=a.id,
                slot_id=a.slot_id,
                standard_minutes=a.standard_minutes,
                units=a.units,
                status=a.status,
            )
            for a in data.allocations
        ],
        reservations=[
            ReservationOut(id=r.id, material_id=r.material_id, quantity=r.quantity, status=r.status)
            for r in data.reservations
        ],
        inspections=[
            InspectionOut(
                id=i.id,
                inspection_type=i.inspection_type,
                inspected_units=i.inspected_units,
                defective_units=i.defective_units,
                result=i.result,
                inspected_at=i.inspected_at,
            )
            for i in data.inspections
        ],
        holds=[
            HoldOut(
                id=h.id,
                reason=h.reason,
                status=h.status,
                created_at=h.created_at,
                released_at=h.released_at,
            )
            for h in data.holds
        ],
        latest_run=(
            LatestRunOut(
                id=data.latest_run.id,
                status=data.latest_run.status,
                created_at=data.latest_run.created_at,
            )
            if data.latest_run is not None
            else None
        ),
        latest_report=data.latest_report,
        allowed_transitions=data.allowed_transitions,
    )


async def idempotent_start(
    session: AsyncSession,
    principal: Principal,
    *,
    operation: str,
    key: str,
    request_payload: dict[str, Any],
) -> JSONResponse | None:
    stored: StoredResponse | None = await idempotency_begin(
        session,
        organization_id=principal.organization_id,
        actor_id=str(principal.user_id),
        operation=operation,
        key=key,
        request_payload=request_payload,
    )
    if stored is None:
        return None
    return JSONResponse(status_code=stored.status_code, content=stored.body)


async def idempotent_finish(
    session: AsyncSession,
    principal: Principal,
    *,
    operation: str,
    key: str,
    status_code: int,
    body: Any,
) -> JSONResponse:
    payload = jsonable_encoder(body)
    await idempotency_finish(
        session,
        organization_id=principal.organization_id,
        actor_id=str(principal.user_id),
        operation=operation,
        key=key,
        status_code=status_code,
        body=payload,
    )
    return JSONResponse(status_code=status_code, content=payload)


def request_trace_id(request: Request) -> str | None:
    """The request's trace ID (set by `TraceIdMiddleware`), if any.

    Passed explicitly to every `record_audit`/`audit_denied` call in this
    task's routes rather than relying on `record_audit`'s own
    `trace_id_var` context-variable fallback.
    """
    trace_id = getattr(request.state, "trace_id", None)
    return str(trace_id) if trace_id else None


async def audit_denial_from_error(
    request: Request,
    principal: Principal,
    exc: AppError,
    *,
    factory_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str,
) -> None:
    """Record a `DENIED` audit event for a 403 raised by a write command.

    Uses its own committed transaction (`app.audit.service.audit_denied`)
    because the request's own transaction is rolled back by the error that
    denies it. A no-op for anything other than 403 `FORBIDDEN`.
    """
    if exc.status_code != 403:
        return
    settings = request.app.state.settings
    session_factory = get_session_factory(settings.database_url)
    await audit_denied(
        session_factory,
        organization_id=principal.organization_id,
        factory_id=factory_id,
        actor_id=str(principal.user_id),
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=exc.message,
        trace_id=request_trace_id(request),
    )


async def _order_factory_id(session: AsyncSession, order_id: uuid.UUID) -> uuid.UUID | None:
    result: uuid.UUID | None = await session.scalar(
        select(Order.factory_id).where(Order.id == order_id)
    )
    return result


@router.get("/factories/{factory_id}/orders", response_model=Page[OrderSummary])
async def list_orders(
    factory_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=100),
    production_state: ProductionState | None = Query(default=None),
    material_state: MaterialState | None = Query(default=None),
    quality_state: QualityState | None = Query(default=None),
    due_before: date | None = Query(default=None),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[OrderSummary]:
    rows, total = await orders_service.list_orders(
        session,
        principal,
        factory_id,
        q=q,
        production_state=production_state.value if production_state else None,
        material_state=material_state.value if material_state else None,
        quality_state=quality_state.value if quality_state else None,
        due_before=due_before,
        limit=page.limit,
        offset=page.offset,
    )
    items = [_to_summary(row.order, row.customer, row.style, row.shipment) for row in rows]
    return Page[OrderSummary](items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/factories/{factory_id}/orders", response_model=OrderSummary, status_code=201)
async def create_order(
    factory_id: uuid.UUID,
    body: OrderCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    request_payload = {"factory_id": str(factory_id), **body.model_dump()}
    early = await idempotent_start(
        session,
        principal,
        operation="order:create",
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early

    try:
        order = await orders_service.create_order(
            session,
            principal,
            factory_id,
            orders_service.NewOrderInput(
                external_ref=body.external_ref,
                customer_id=body.customer_id,
                style_id=body.style_id,
                quantity=body.quantity,
                due_date=body.due_date,
                priority=body.priority,
            ),
            trace_id=request_trace_id(request),
        )
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=factory_id,
            action="order.create",
            target_type="factory",
            target_id=str(factory_id),
        )
        raise
    customer = await session.get(Customer, order.customer_id)
    style = await session.get(Style, order.style_id)
    if customer is None or style is None:
        raise RuntimeError("newly created order references a missing customer or style")
    shipment = await orders_service.compute_shipment(session, order)
    summary = _to_summary(order, customer, style, shipment)

    return await idempotent_finish(
        session,
        principal,
        operation="order:create",
        key=idempotency_key,
        status_code=201,
        body=summary,
    )


ACTOR_TYPE_LABELS: dict[str, str] = {
    ActorType.SERVICE.value: "Service",
    ActorType.SYSTEM.value: "System",
}


UNKNOWN_USER_LABEL = "Unknown user"


async def _actor_display_names(
    session: AsyncSession, events: Sequence[AuditEvent]
) -> dict[int, str]:
    """Resolves every event's actor to a display name with one extra query.

    User actors are stored as the user's UUID (`str(principal.user_id)`);
    service/system actors use a fixed non-UUID id (e.g. "orchestrator"), so
    only `USER` events are ever looked up, and all of them in a single
    `SELECT ... WHERE id IN (...)` rather than one `session.get` per row.
    """
    user_ids: set[uuid.UUID] = set()
    for event in events:
        if event.actor_type != ActorType.USER.value:
            continue
        try:
            user_ids.add(uuid.UUID(event.actor_id))
        except ValueError:
            continue

    display_names: dict[uuid.UUID, str] = {}
    if user_ids:
        users = (await session.scalars(select(User).where(User.id.in_(user_ids)))).all()
        display_names = {user.id: user.display_name for user in users}

    result: dict[int, str] = {}
    for event in events:
        if event.actor_type != ActorType.USER.value:
            result[event.id] = ACTOR_TYPE_LABELS.get(event.actor_type, event.actor_type)
            continue
        try:
            user_id = uuid.UUID(event.actor_id)
        except ValueError:
            result[event.id] = UNKNOWN_USER_LABEL
            continue
        result[event.id] = display_names.get(user_id, UNKNOWN_USER_LABEL)
    return result


@router.get("/orders/{order_id}/history", response_model=Page[OrderHistoryEventOut])
async def order_history(
    order_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[OrderHistoryEventOut]:
    """Audit events recorded against this order (`order:read`).

    Scoped to events with `target_type == "order"` and `target_id ==
    str(order_id)`: the events every order lifecycle write (create,
    transition, progress, and their denials) already records in
    `app.domain.orders.service`/this module against the order itself.
    """
    await load_scoped(session, Order, order_id, principal, "order:read")
    base = select(AuditEvent).where(
        AuditEvent.organization_id == principal.organization_id,
        AuditEvent.target_type == "order",
        AuditEvent.target_id == str(order_id),
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await session.scalars(
            base.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    display_names = await _actor_display_names(session, rows)
    items = [
        OrderHistoryEventOut(
            id=row.id,
            actor_display_name=display_names[row.id],
            action=row.action,
            outcome=row.outcome,
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return Page[OrderHistoryEventOut](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )


@router.get("/orders/{order_id}", response_model=OrderDetail)
async def get_order(
    order_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> OrderDetail:
    data = await orders_service.order_detail(session, principal, order_id)
    return _to_detail(data)


@router.post("/orders/{order_id}/transitions", response_model=OrderDetail)
async def transition_order(
    order_id: uuid.UUID,
    body: OrderTransitionRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    request_payload = {"order_id": str(order_id), **body.model_dump()}
    early = await idempotent_start(
        session,
        principal,
        operation="order:transition",
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early

    try:
        await orders_service.transition_order(
            session,
            principal,
            order_id,
            body.target_state,
            body.expected_version,
            body.reason,
            trace_id=request_trace_id(request),
        )
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await _order_factory_id(session, order_id),
            action="order.transition",
            target_type="order",
            target_id=str(order_id),
        )
        raise
    data = await orders_service.order_detail(session, principal, order_id)
    detail = _to_detail(data)

    return await idempotent_finish(
        session,
        principal,
        operation="order:transition",
        key=idempotency_key,
        status_code=200,
        body=detail,
    )


@router.post("/orders/{order_id}/progress", response_model=OrderDetail)
async def progress_order(
    order_id: uuid.UUID,
    body: OrderProgressRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    request_payload = {"order_id": str(order_id), **body.model_dump()}
    early = await idempotent_start(
        session,
        principal,
        operation="order:progress",
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early

    try:
        await orders_service.progress_order(
            session,
            principal,
            order_id,
            produced_units=body.produced_units,
            packed_units=body.packed_units,
            expected_version=body.expected_version,
            trace_id=request_trace_id(request),
        )
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=await _order_factory_id(session, order_id),
            action="order.progress",
            target_type="order",
            target_id=str(order_id),
        )
        raise
    data = await orders_service.order_detail(session, principal, order_id)
    detail = _to_detail(data)

    return await idempotent_finish(
        session,
        principal,
        operation="order:progress",
        key=idempotency_key,
        status_code=200,
        body=detail,
    )
