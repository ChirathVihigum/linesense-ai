"""Inventory read models: material overview, ledger and reservation lists
(task-8-brief.md requirement 3)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import (
    ExpectedReceipt,
    Factory,
    Material,
    MaterialBalance,
    MaterialLot,
    Reservation,
    StockMovement,
)
from app.domain.inventory.calc import (
    available_now,
    average_daily_consumption,
    coverage_days,
    reorder_point,
)
from app.domain.rounding import quantize_display
from app.domain.vocab import ExpectedReceiptStatus, MovementType

STATUS_SOURCE = "Calculated from records"
CONSUMPTION_WINDOW_DAYS = 14


@dataclass(frozen=True)
class MaterialStatusRow:
    material_id: uuid.UUID
    material_code: str
    material_name: str
    unit: str
    on_hand: Decimal
    reserved: Decimal
    available_now: Decimal
    open_receipt_quantity: Decimal
    next_receipt_date: date | None
    average_daily_consumption: Decimal
    coverage_days: Decimal | None
    reorder_point: Decimal
    below_reorder_point: bool
    balance_version: int | None
    status_source: str = STATUS_SOURCE


@dataclass(frozen=True)
class LedgerRow:
    movement: StockMovement
    lot_code: str | None


async def scoped_material(
    session: AsyncSession, principal: Principal, factory_id: uuid.UUID, material_id: uuid.UUID
) -> Material:
    """A material addressed by path: 404 unless it belongs to the principal's org."""
    await load_scoped(session, Factory, factory_id, principal, "inventory:read")
    material = await session.get(Material, material_id)
    if material is None or material.organization_id != principal.organization_id:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    return material


async def list_ledger(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    material_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[LedgerRow], int]:
    """A material's movements in the factory, newest first."""
    material = await scoped_material(session, principal, factory_id, material_id)
    conditions = [
        StockMovement.factory_id == factory_id,
        StockMovement.material_id == material.id,
    ]
    total = await session.scalar(
        select(sa.func.count()).select_from(StockMovement).where(*conditions)
    )
    rows = (
        await session.execute(
            select(StockMovement, MaterialLot.lot_code)
            .outerjoin(MaterialLot, MaterialLot.id == StockMovement.lot_id)
            .where(*conditions)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [LedgerRow(movement=m, lot_code=code) for m, code in rows], int(total or 0)


async def list_reservations(
    session: AsyncSession,
    principal: Principal,
    factory_id: uuid.UUID,
    *,
    order_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[Reservation], int]:
    await load_scoped(session, Factory, factory_id, principal, "inventory:read")
    conditions: list[Any] = [Reservation.factory_id == factory_id]
    if order_id is not None:
        conditions.append(Reservation.order_id == order_id)
    total = await session.scalar(
        select(sa.func.count()).select_from(Reservation).where(*conditions)
    )
    rows = (
        await session.scalars(
            select(Reservation)
            .where(*conditions)
            .order_by(Reservation.created_at.desc(), Reservation.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(rows), int(total or 0)


async def daily_issues(
    session: AsyncSession,
    factory: Factory,
    as_of: date,
    material_ids: Collection[uuid.UUID] | None = None,
) -> dict[uuid.UUID, list[tuple[date, Decimal]]]:
    """Signed ISSUE totals per material and factory-local day, from
    `as_of - (CONSUMPTION_WINDOW_DAYS + 1)` days onward (callers window them
    precisely, e.g. with `average_daily_consumption`)."""
    local_date = sa.cast(sa.func.timezone(factory.timezone, StockMovement.created_at), sa.Date)
    conditions: list[Any] = [
        StockMovement.factory_id == factory.id,
        StockMovement.movement_type == MovementType.ISSUE.value,
        StockMovement.created_at
        >= sa.func.timezone(
            factory.timezone,
            sa.cast(as_of - timedelta(days=CONSUMPTION_WINDOW_DAYS + 1), sa.DateTime),
        ),
    ]
    if material_ids is not None:
        conditions.append(StockMovement.material_id.in_(list(material_ids)))
    issues: dict[uuid.UUID, list[tuple[date, Decimal]]] = defaultdict(list)
    for material_id, issued_on, total in (
        await session.execute(
            select(StockMovement.material_id, local_date, sa.func.sum(StockMovement.quantity))
            .where(*conditions)
            .group_by(StockMovement.material_id, local_date)
            .order_by(StockMovement.material_id, local_date)
        )
    ).all():
        issues[material_id].append((issued_on, Decimal(total)))
    return issues


async def material_overview(
    session: AsyncSession, factory_id: uuid.UUID, as_of: date
) -> list[MaterialStatusRow]:
    """Per material of the factory's organization (by code): stock position,
    open receipts, 14-day average consumption, coverage and reorder point.

    Consumption is the ISSUE movements whose factory-local date lies in
    `(as_of - 14 days, as_of]`. Coverage is `None` (unknown) without
    consumption. Averages/coverage are rounded for display only.
    """
    factory = await session.get(Factory, factory_id)
    if factory is None:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    materials = (
        await session.scalars(
            select(Material)
            .where(Material.organization_id == factory.organization_id)
            .order_by(Material.code)
        )
    ).all()
    balances = {
        balance.material_id: balance
        for balance in (
            await session.scalars(
                select(MaterialBalance).where(MaterialBalance.factory_id == factory_id)
            )
        ).all()
    }
    open_receipts: dict[uuid.UUID, tuple[Decimal, date]] = {
        material_id: (Decimal(total), first_date)
        for material_id, total, first_date in (
            await session.execute(
                select(
                    ExpectedReceipt.material_id,
                    sa.func.sum(ExpectedReceipt.quantity),
                    sa.func.min(ExpectedReceipt.expected_date),
                )
                .where(
                    ExpectedReceipt.factory_id == factory_id,
                    ExpectedReceipt.status == ExpectedReceiptStatus.OPEN.value,
                )
                .group_by(ExpectedReceipt.material_id)
            )
        ).all()
    }

    issues = await daily_issues(session, factory, as_of)

    rows: list[MaterialStatusRow] = []
    for material in materials:
        balance = balances.get(material.id)
        on_hand = balance.on_hand_accepted if balance is not None else Decimal(0)
        reserved = balance.reserved if balance is not None else Decimal(0)
        available = available_now(on_hand, reserved)
        daily = average_daily_consumption(
            issues.get(material.id, []), CONSUMPTION_WINDOW_DAYS, as_of
        )
        coverage = coverage_days(available, daily)
        rop = reorder_point(daily, material.lead_time_days, material.safety_stock)
        receipt_total, next_date = open_receipts.get(material.id, (Decimal(0), None))
        rows.append(
            MaterialStatusRow(
                material_id=material.id,
                material_code=material.code,
                material_name=material.name,
                unit=material.unit,
                on_hand=on_hand,
                reserved=reserved,
                available_now=available,
                open_receipt_quantity=receipt_total,
                next_receipt_date=next_date,
                average_daily_consumption=quantize_display(daily, 4),
                coverage_days=quantize_display(coverage, 2) if coverage is not None else None,
                reorder_point=quantize_display(rop, 4),
                below_reorder_point=available < rop,
                balance_version=balance.version if balance is not None else None,
            )
        )
    return rows
