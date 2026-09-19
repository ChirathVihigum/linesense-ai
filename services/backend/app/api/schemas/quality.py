"""Request/response schemas for `app.api.quality`."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

DEMO_POLICY_LABEL = "Demo policy — not a certified AQL standard"


class DefectIn(BaseModel):
    defect_code: str = Field(min_length=1, max_length=64)
    severity: Literal["MINOR", "MAJOR", "CRITICAL"]
    count: int = Field(gt=0)
    operation_id: uuid.UUID | None = None


class DefectOut(BaseModel):
    defect_code: str
    severity: str
    count: int
    operation_id: uuid.UUID | None


class InspectionCreate(BaseModel):
    inspection_type: Literal["INLINE", "FINAL"]
    inspected_units: int = Field(ge=0)
    defective_units: int = Field(ge=0)
    defects: list[DefectIn] = Field(default_factory=list)
    line_id: uuid.UUID | None = None


class QualityInspectionOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    line_id: uuid.UUID | None
    inspection_type: str
    inspected_units: int
    defective_units: int
    policy_version_id: uuid.UUID
    result: str
    inspected_by: uuid.UUID | None
    inspected_at: datetime
    defects: list[DefectOut]


class HoldCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class QualityHoldOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    inspection_id: uuid.UUID | None
    reason: str
    status: str
    created_by: uuid.UUID | None
    created_at: datetime
    released_by: uuid.UUID | None
    released_at: datetime | None
    release_id: uuid.UUID | None


class ReleaseCreate(BaseModel):
    inspection_id: uuid.UUID
    expected_order_version: int = Field(ge=1)
    notes: str | None = Field(default=None, max_length=1000)


class ReleaseOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    inspection_id: uuid.UUID
    policy_version_id: uuid.UUID
    released_by: uuid.UUID
    released_at: datetime
    notes: str | None


class ShipmentOut(BaseModel):
    eligible: bool
    reasons: list[str]


class PolicyOut(BaseModel):
    id: uuid.UUID
    code: str
    version_no: int
    is_demo: bool
    status: str
    rules: dict[str, Any]
    label: str | None


class OrderQualityOut(BaseModel):
    order_id: uuid.UUID
    order_version: int
    quality_state: str
    inspections: list[QualityInspectionOut]
    holds: list[QualityHoldOut]
    releases: list[ReleaseOut]
    shipment: ShipmentOut
    policy: PolicyOut | None


class DefectCodeTrendOut(BaseModel):
    defect_code: str
    severity: str
    count: int


class DefectTrendOut(BaseModel):
    factory_id: uuid.UUID
    window_days: int
    inspected_units: int
    defective_units: int
    defective_rate: Decimal | None
    defects_per_hundred_units: Decimal | None
    by_defect_code: list[DefectCodeTrendOut]
