"""Capacity routes: production lines and the capacity board
(backend-contracts.md sections 4-5; task-8-brief.md).

Read-only: allocations are created only by applying an approved
recommendation (Task 14).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.capacity import (
    BoardLineOut,
    CapacityBoardOut,
    LineOut,
    SlotAllocationOut,
    SlotOut,
)
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Factory
from app.db.session import get_db_session
from app.domain.capacity import service as capacity_service
from app.domain.rounding import quantize_display

router = APIRouter(prefix="/api/v1", tags=["capacity"])

UTILIZATION_PLACES = 4


@router.get("/factories/{factory_id}/lines", response_model=Page[LineOut])
async def list_lines(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[LineOut]:
    factory = await load_scoped(session, Factory, factory_id, principal, "capacity:read")
    rows, total = await capacity_service.list_lines(
        session, factory.id, limit=page.limit, offset=page.offset
    )
    return Page[LineOut](
        items=[
            LineOut(
                id=line.id,
                code=line.code,
                name=line.name,
                operator_count=line.operator_count,
                is_active=line.is_active,
                skill_codes=skills,
            )
            for line, skills in rows
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/factories/{factory_id}/capacity", response_model=CapacityBoardOut)
async def capacity_board(
    factory_id: uuid.UUID,
    start: date = Query(),
    end: date = Query(),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> CapacityBoardOut:
    factory = await load_scoped(session, Factory, factory_id, principal, "capacity:read")
    board = await capacity_service.capacity_board(session, factory.id, start, end)
    return CapacityBoardOut(
        factory_id=board.factory_id,
        start=board.start,
        end=board.end,
        lines=[
            BoardLineOut(
                id=line.id,
                code=line.code,
                name=line.name,
                operator_count=line.operator_count,
                is_active=line.is_active,
                slots=[
                    SlotOut(
                        id=slot.id,
                        slot_date=slot.slot_date,
                        shift_code=slot.shift_code,
                        available_operator_minutes=slot.available_operator_minutes,
                        planned_efficiency=slot.planned_efficiency,
                        capacity_standard_minutes=slot.capacity_standard_minutes,
                        allocated_standard_minutes=slot.allocated_standard_minutes,
                        remaining_standard_minutes=slot.remaining_standard_minutes,
                        utilization=(
                            quantize_display(slot.utilization, UTILIZATION_PLACES)
                            if slot.utilization is not None
                            else None
                        ),
                        version=slot.version,
                        allocations=[
                            SlotAllocationOut(
                                id=allocation.id,
                                order_id=allocation.order_id,
                                order_external_ref=allocation.order_external_ref,
                                standard_minutes=allocation.standard_minutes,
                                units=allocation.units,
                            )
                            for allocation in slot.allocations
                        ],
                    )
                    for slot in line.slots
                ],
            )
            for line in board.lines
        ],
    )
