"""Inventory routes: material overview, ledger, stock movements and
reservations (backend-contracts.md sections 4-5; task-8-brief.md).

Reads need `inventory:read`; every write needs `inventory:write`
(storekeeper) and an `Idempotency-Key`.
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
from app.api.schemas.inventory import (
    BalanceOut,
    CorrectionCreate,
    IssueCreate,
    LotAcceptResult,
    LotOut,
    MaterialOverview,
    MaterialReservationOut,
    MaterialStatusOut,
    MovementOut,
    ReceiptCreate,
    ReservationCommandResult,
    ReservationCreate,
    StockCommandResult,
)
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    Factory,
    MaterialBalance,
    MaterialLot,
    Reservation,
    StockMovement,
)
from app.db.session import get_db_session
from app.domain.clock import today_in
from app.domain.inventory import service as inventory_service
from app.domain.inventory.calc import available_now

router = APIRouter(prefix="/api/v1", tags=["inventory"])


def _balance_out(balance: MaterialBalance) -> BalanceOut:
    return BalanceOut(
        material_id=balance.material_id,
        on_hand_accepted=balance.on_hand_accepted,
        reserved=balance.reserved,
        available_now=available_now(balance.on_hand_accepted, balance.reserved),
        version=balance.version,
    )


def _lot_out(lot: MaterialLot) -> LotOut:
    return LotOut(
        id=lot.id,
        lot_code=lot.lot_code,
        material_id=lot.material_id,
        status=lot.status,
        received_at=lot.received_at,
    )


def _movement_out(movement: StockMovement, lot_code: str | None) -> MovementOut:
    return MovementOut(
        id=movement.id,
        material_id=movement.material_id,
        lot_id=movement.lot_id,
        lot_code=lot_code,
        movement_type=movement.movement_type,
        quantity=movement.quantity,
        corrects_movement_id=movement.corrects_movement_id,
        reason=movement.reason,
        order_id=movement.order_id,
        created_by=movement.created_by,
        created_at=movement.created_at,
    )


def _reservation_out(reservation: Reservation) -> MaterialReservationOut:
    return MaterialReservationOut(
        id=reservation.id,
        material_id=reservation.material_id,
        order_id=reservation.order_id,
        quantity=reservation.quantity,
        status=reservation.status,
        recommendation_id=reservation.recommendation_id,
        created_by=reservation.created_by,
        created_at=reservation.created_at,
    )


async def _current_balance(
    session: AsyncSession, factory_id: uuid.UUID, material_id: uuid.UUID
) -> MaterialBalance:
    balance = await inventory_service.get_balance(session, factory_id, material_id)
    if balance is None:
        raise RuntimeError("a completed inventory command left no material balance")
    await session.refresh(balance)
    return balance


async def _stock_result(session: AsyncSession, movement: StockMovement) -> StockCommandResult:
    await session.refresh(movement)
    if movement.lot_id is None:
        raise RuntimeError("stock commands always record a lot")
    lot = await session.get(MaterialLot, movement.lot_id)
    if lot is None:
        raise RuntimeError("stock movement references a missing lot")
    balance = await _current_balance(session, movement.factory_id, movement.material_id)
    return StockCommandResult(
        movement=_movement_out(movement, lot.lot_code),
        lot=_lot_out(lot),
        balance=_balance_out(balance),
    )


async def _reservation_result(
    session: AsyncSession, reservation: Reservation
) -> ReservationCommandResult:
    await session.refresh(reservation)
    balance = await _current_balance(session, reservation.factory_id, reservation.material_id)
    return ReservationCommandResult(
        reservation=_reservation_out(reservation), balance=_balance_out(balance)
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
    """Idempotency + denied-audit wrapper shared by every inventory write."""
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
    session: AsyncSession, model: Any, row_id: uuid.UUID
) -> Callable[[], Awaitable[uuid.UUID | None]]:
    async def resolve() -> uuid.UUID | None:
        result: uuid.UUID | None = await session.scalar(
            select(model.factory_id).where(model.id == row_id)
        )
        return result

    return resolve


@router.get("/factories/{factory_id}/materials", response_model=MaterialOverview)
async def material_overview(
    factory_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> MaterialOverview:
    factory = await load_scoped(session, Factory, factory_id, principal, "inventory:read")
    as_of = today_in(factory.timezone)
    rows = await inventory_service.material_overview(session, factory.id, as_of)
    return MaterialOverview(
        as_of=as_of,
        items=[
            MaterialStatusOut(
                material_id=row.material_id,
                material_code=row.material_code,
                material_name=row.material_name,
                unit=row.unit,
                on_hand=row.on_hand,
                reserved=row.reserved,
                available_now=row.available_now,
                open_receipt_quantity=row.open_receipt_quantity,
                next_receipt_date=row.next_receipt_date,
                average_daily_consumption=row.average_daily_consumption,
                coverage_days=row.coverage_days,
                reorder_point=row.reorder_point,
                below_reorder_point=row.below_reorder_point,
                balance_version=row.balance_version,
                status_source=row.status_source,
            )
            for row in rows
        ],
    )


@router.get(
    "/factories/{factory_id}/materials/{material_id}/ledger", response_model=Page[MovementOut]
)
async def material_ledger(
    factory_id: uuid.UUID,
    material_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[MovementOut]:
    rows, total = await inventory_service.list_ledger(
        session, principal, factory_id, material_id, limit=page.limit, offset=page.offset
    )
    return Page[MovementOut](
        items=[_movement_out(row.movement, row.lot_code) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/factories/{factory_id}/stock/receipts", response_model=StockCommandResult, status_code=201
)
async def record_receipt(
    factory_id: uuid.UUID,
    body: ReceiptCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> StockCommandResult:
        movement = await inventory_service.record_receipt(
            session,
            principal,
            factory_id,
            material_id=body.material_id,
            lot_code=body.lot_code,
            quantity=body.quantity,
            accept=body.accept,
        )
        return await _stock_result(session, movement)

    return await _run_command(
        request,
        session,
        principal,
        operation="inventory:receipt",
        idempotency_key=idempotency_key,
        request_payload={"factory_id": str(factory_id), **body.model_dump()},
        status_code=201,
        audit_action="inventory.receipt",
        audit_target_type="stock_movement",
        audit_target_id=str(factory_id),
        audit_factory_id=_fixed(factory_id),
        command=command,
    )


@router.post("/factories/{factory_id}/stock/lots/{lot_id}/accept", response_model=LotAcceptResult)
async def accept_lot(
    factory_id: uuid.UUID,
    lot_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> LotAcceptResult:
        await load_scoped(session, Factory, factory_id, principal, "inventory:write")
        lot = await session.get(MaterialLot, lot_id)
        if lot is None or lot.factory_id != factory_id:
            raise AppError(404, "NOT_FOUND", "Resource not found.")
        lot = await inventory_service.accept_lot(session, principal, lot_id)
        balance = await _current_balance(session, lot.factory_id, lot.material_id)
        return LotAcceptResult(lot=_lot_out(lot), balance=_balance_out(balance))

    return await _run_command(
        request,
        session,
        principal,
        operation="inventory:lot_accept",
        idempotency_key=idempotency_key,
        request_payload={"factory_id": str(factory_id), "lot_id": str(lot_id)},
        status_code=200,
        audit_action="inventory.lot_accept",
        audit_target_type="material_lot",
        audit_target_id=str(lot_id),
        audit_factory_id=_fixed(factory_id),
        command=command,
    )


@router.post(
    "/factories/{factory_id}/stock/issues", response_model=StockCommandResult, status_code=201
)
async def record_issue(
    factory_id: uuid.UUID,
    body: IssueCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> StockCommandResult:
        movement = await inventory_service.record_issue(
            session,
            principal,
            factory_id,
            material_id=body.material_id,
            lot_id=body.lot_id,
            quantity=body.quantity,
            order_id=body.order_id,
            reason=body.reason,
        )
        return await _stock_result(session, movement)

    return await _run_command(
        request,
        session,
        principal,
        operation="inventory:issue",
        idempotency_key=idempotency_key,
        request_payload={"factory_id": str(factory_id), **body.model_dump()},
        status_code=201,
        audit_action="inventory.issue",
        audit_target_type="stock_movement",
        audit_target_id=str(factory_id),
        audit_factory_id=_fixed(factory_id),
        command=command,
    )


@router.post(
    "/factories/{factory_id}/stock/corrections",
    response_model=StockCommandResult,
    status_code=201,
)
async def record_correction(
    factory_id: uuid.UUID,
    body: CorrectionCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> StockCommandResult:
        await load_scoped(session, Factory, factory_id, principal, "inventory:write")
        original = await session.get(StockMovement, body.movement_id)
        if original is None or original.factory_id != factory_id:
            raise AppError(404, "NOT_FOUND", "Resource not found.")
        movement = await inventory_service.record_correction(
            session,
            principal,
            movement_id=body.movement_id,
            quantity_delta=body.quantity_delta,
            reason=body.reason,
        )
        return await _stock_result(session, movement)

    return await _run_command(
        request,
        session,
        principal,
        operation="inventory:correction",
        idempotency_key=idempotency_key,
        request_payload={"factory_id": str(factory_id), **body.model_dump()},
        status_code=201,
        audit_action="inventory.correction",
        audit_target_type="stock_movement",
        audit_target_id=str(body.movement_id),
        audit_factory_id=_fixed(factory_id),
        command=command,
    )


@router.get("/factories/{factory_id}/reservations", response_model=Page[MaterialReservationOut])
async def list_reservations(
    factory_id: uuid.UUID,
    order_id: uuid.UUID | None = Query(default=None),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[MaterialReservationOut]:
    rows, total = await inventory_service.list_reservations(
        session, principal, factory_id, order_id=order_id, limit=page.limit, offset=page.offset
    )
    return Page[MaterialReservationOut](
        items=[_reservation_out(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/factories/{factory_id}/reservations",
    response_model=ReservationCommandResult,
    status_code=201,
)
async def create_reservation(
    factory_id: uuid.UUID,
    body: ReservationCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> ReservationCommandResult:
        reservation = await inventory_service.create_reservation(
            session,
            principal,
            factory_id,
            material_id=body.material_id,
            order_id=body.order_id,
            quantity=body.quantity,
        )
        return await _reservation_result(session, reservation)

    return await _run_command(
        request,
        session,
        principal,
        operation="inventory:reserve",
        idempotency_key=idempotency_key,
        request_payload={"factory_id": str(factory_id), **body.model_dump()},
        status_code=201,
        audit_action="inventory.reserve",
        audit_target_type="reservation",
        audit_target_id=str(body.order_id),
        audit_factory_id=_fixed(factory_id),
        command=command,
    )


@router.post("/reservations/{reservation_id}/release", response_model=ReservationCommandResult)
async def release_reservation(
    reservation_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> ReservationCommandResult:
        reservation = await inventory_service.release_reservation(
            session, principal, reservation_id
        )
        return await _reservation_result(session, reservation)

    return await _run_command(
        request,
        session,
        principal,
        operation="inventory:release",
        idempotency_key=idempotency_key,
        request_payload={"reservation_id": str(reservation_id)},
        status_code=200,
        audit_action="inventory.release",
        audit_target_type="reservation",
        audit_target_id=str(reservation_id),
        audit_factory_id=_factory_of(session, Reservation, reservation_id),
        command=command,
    )
