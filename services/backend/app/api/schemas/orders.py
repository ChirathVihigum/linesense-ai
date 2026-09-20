"""Request/response schemas for `app.api.orders` and `app.api.reference`."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

EXTERNAL_REF_PATTERN = r"^[A-Z0-9][A-Z0-9-]*$"


class CustomerRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class StyleRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class FactoryRef(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class CustomerOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class StyleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    product_type: str


class ShipmentStatus(BaseModel):
    eligible: bool
    reasons: list[str]


class OrderSummary(BaseModel):
    id: uuid.UUID
    external_ref: str
    customer: CustomerRef
    style: StyleRef
    quantity: int
    produced_units: int
    packed_units: int
    due_date: date
    priority: int
    production_state: str
    material_state: str
    quality_state: str
    shipment: ShipmentStatus
    version: int
    updated_at: datetime


class BomLineOut(BaseModel):
    material_code: str
    material_name: str
    quantity_per_unit: Decimal
    unit: str
    wastage_fraction: Decimal


class BomOut(BaseModel):
    version_no: int
    lines: list[BomLineOut]


class OperationOut(BaseModel):
    sequence: int
    code: str
    name: str
    sam_minutes: Decimal


class AllocationOut(BaseModel):
    id: uuid.UUID
    slot_id: uuid.UUID
    standard_minutes: Decimal
    units: Decimal
    status: str


class ReservationOut(BaseModel):
    id: uuid.UUID
    material_id: uuid.UUID
    quantity: Decimal
    status: str


class InspectionOut(BaseModel):
    id: uuid.UUID
    inspection_type: str
    inspected_units: int
    defective_units: int
    result: str
    inspected_at: datetime


class HoldOut(BaseModel):
    id: uuid.UUID
    reason: str
    status: str
    created_at: datetime
    released_at: datetime | None


class LatestRunOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime


class OrderDetail(OrderSummary):
    factory: FactoryRef
    bom: BomOut
    operations: list[OperationOut]
    allocations: list[AllocationOut]
    reservations: list[ReservationOut]
    inspections: list[InspectionOut]
    holds: list[HoldOut]
    latest_run: LatestRunOut | None
    # The latest finalized run's canonical order report (``run.report``), with a
    # ``stale`` flag when the order has changed since that run's snapshot.
    latest_report: dict[str, Any] | None
    allowed_transitions: list[str]


class OrderCreate(BaseModel):
    external_ref: str = Field(min_length=3, max_length=40, pattern=EXTERNAL_REF_PATTERN)
    customer_id: uuid.UUID
    style_id: uuid.UUID
    quantity: int = Field(ge=1, le=1_000_000)
    due_date: date
    priority: int = Field(ge=1, le=5)


class OrderTransitionRequest(BaseModel):
    target_state: str
    expected_version: int
    reason: str | None = Field(default=None, max_length=500)


class OrderProgressRequest(BaseModel):
    produced_units: int = Field(ge=0)
    packed_units: int = Field(ge=0)
    expected_version: int


class OrderHistoryEventOut(BaseModel):
    id: int
    actor_display_name: str
    action: str
    outcome: str
    reason: str | None
    created_at: datetime
