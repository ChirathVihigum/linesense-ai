"""Request/response schemas for `app.api.inventory`."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

LOT_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"
_MAX_QUANTITY = Decimal("9999999999.9999")


class BalanceOut(BaseModel):
    material_id: uuid.UUID
    on_hand_accepted: Decimal
    reserved: Decimal
    available_now: Decimal
    version: int


class LotOut(BaseModel):
    id: uuid.UUID
    lot_code: str
    material_id: uuid.UUID
    status: str
    received_at: datetime


class MovementOut(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    lot_id: uuid.UUID | None
    lot_code: str | None
    movement_type: str
    quantity: Decimal
    corrects_movement_id: uuid.UUID | None
    reason: str | None
    order_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime


class StockCommandResult(BaseModel):
    movement: MovementOut
    lot: LotOut
    balance: BalanceOut


class LotAcceptResult(BaseModel):
    lot: LotOut
    balance: BalanceOut


class MaterialReservationOut(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    order_id: uuid.UUID
    quantity: Decimal
    status: str
    recommendation_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime


class ReservationCommandResult(BaseModel):
    reservation: MaterialReservationOut
    balance: BalanceOut


class MaterialStatusOut(BaseModel):
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
    coverage_days: Decimal | None = Field(
        description="Days of cover at the 14-day average consumption; null means unknown."
    )
    reorder_point: Decimal
    below_reorder_point: bool
    balance_version: int | None
    status_source: str


class MaterialOverview(BaseModel):
    as_of: date
    items: list[MaterialStatusOut]


class ReceiptCreate(BaseModel):
    material_id: uuid.UUID
    lot_code: str = Field(min_length=1, max_length=64, pattern=LOT_CODE_PATTERN)
    quantity: Decimal = Field(gt=0, le=_MAX_QUANTITY, decimal_places=4)
    accept: bool


class IssueCreate(BaseModel):
    material_id: uuid.UUID
    lot_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=_MAX_QUANTITY, decimal_places=4)
    order_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class CorrectionCreate(BaseModel):
    movement_id: uuid.UUID
    quantity_delta: Decimal = Field(ge=-_MAX_QUANTITY, le=_MAX_QUANTITY, decimal_places=4)
    reason: str = Field(min_length=3, max_length=500)


class ReservationCreate(BaseModel):
    material_id: uuid.UUID
    order_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=_MAX_QUANTITY, decimal_places=4)
