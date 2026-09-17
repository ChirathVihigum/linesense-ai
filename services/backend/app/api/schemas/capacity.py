"""Response schemas for `app.api.capacity`."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class LineOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    operator_count: int
    is_active: bool
    skill_codes: list[str]


class SlotAllocationOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_external_ref: str
    standard_minutes: Decimal
    units: Decimal


class SlotOut(BaseModel):
    id: uuid.UUID
    slot_date: date
    shift_code: str
    available_operator_minutes: Decimal
    planned_efficiency: Decimal
    capacity_standard_minutes: Decimal
    allocated_standard_minutes: Decimal
    remaining_standard_minutes: Decimal
    utilization: Decimal | None
    version: int
    allocations: list[SlotAllocationOut]


class BoardLineOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    operator_count: int
    is_active: bool
    slots: list[SlotOut]


class CapacityBoardOut(BaseModel):
    factory_id: uuid.UUID
    start: date
    end: date
    lines: list[BoardLineOut]
