"""Response schemas for `app.api.dashboard` (task-22-brief.md requirement 1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class OrderAtRiskOut(BaseModel):
    id: uuid.UUID
    external_ref: str
    customer_code: str
    style_code: str
    due_date: date
    quantity: int
    produced_units: int
    priority: int
    production_state: str
    material_state: str
    quality_state: str


class MaterialShortageOut(BaseModel):
    material_id: uuid.UUID
    material_code: str
    material_name: str
    unit: str
    available_now: Decimal
    demand: Decimal
    reorder_point: Decimal
    below_reorder_point: bool
    shortage_qty: Decimal


class DashboardQualityHoldOut(BaseModel):
    """A quality hold as shown on the dashboard.

    Named distinctly from `app.api.schemas.quality.QualityHoldOut` (a
    different, larger shape): FastAPI/OpenAPI generates one schema name per
    class, and two classes sharing a name in different modules collide in
    the generated OpenAPI document (`openapi-typescript` then also renames
    the other one, breaking every existing caller of it).
    """

    id: uuid.UUID
    order_id: uuid.UUID
    order_external_ref: str
    reason: str
    created_at: datetime


class ActiveRunOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_external_ref: str
    status: str
    model_calls_used: int
    model_calls_limit: int
    started_at: datetime | None
    deadline_at: datetime


class CapacityLineUtilizationOut(BaseModel):
    line_id: uuid.UUID
    line_code: str
    line_name: str
    capacity_minutes: Decimal
    allocated_minutes: Decimal
    utilization_fraction: Decimal | None


class DashboardOut(BaseModel):
    generated_at: datetime
    status_source: str
    as_of: date
    orders_at_risk: list[OrderAtRiskOut]
    material_shortages: list[MaterialShortageOut]
    quality_holds: list[DashboardQualityHoldOut]
    active_runs: list[ActiveRunOut]
    pending_approvals: int
    capacity_next_7_days: list[CapacityLineUtilizationOut]
