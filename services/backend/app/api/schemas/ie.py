"""Request/response schemas for `app.api.ie`."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ObservationCreate(BaseModel):
    line_id: uuid.UUID
    style_id: uuid.UUID
    operation_id: uuid.UUID
    operator_alias_code: str = Field(min_length=1, max_length=64)
    observed_seconds: Decimal = Field(gt=0, le=3600, decimal_places=2)
    observed_at: datetime


class ObservationOut(BaseModel):
    id: uuid.UUID
    line_id: uuid.UUID
    style_id: uuid.UUID
    operation_id: uuid.UUID
    operator_alias_id: uuid.UUID
    observed_seconds: Decimal
    observed_at: datetime
    is_outlier: bool
    outlier_approved_by: uuid.UUID | None
    recorded_by: uuid.UUID | None
    created_at: datetime


class OutlierMarkRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class OperationAnalysisOut(BaseModel):
    operation_id: uuid.UUID
    code: str
    name: str
    sam_minutes: Decimal
    sample_count: int
    representative_seconds: Decimal | None
    parallel_operators: int
    effective_seconds: Decimal | None
    insufficient_samples: bool


class LineBalanceOut(BaseModel):
    bottleneck_index: int
    bottleneck_effective_seconds: Decimal
    units_per_hour: Decimal
    balance_index_percent: Decimal


class LineStyleAnalysisOut(BaseModel):
    line_id: uuid.UUID
    style_id: uuid.UUID
    operations: list[OperationAnalysisOut]
    balance: LineBalanceOut | None
    observed_units_per_hour: Decimal | None
    sam_units_per_hour: Decimal | None
    assumptions: list[str]
    limitations: list[str]
    data_versions: dict[str, Any]


class OperatorAliasOut(BaseModel):
    id: uuid.UUID
    alias_code: str
    line_id: uuid.UUID | None
    is_active: bool
