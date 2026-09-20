"""Request/response schemas for `app.api.admin` (task-22-brief.md requirement 2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.vocab import ROLES


class RoleAssignmentOut(BaseModel):
    id: uuid.UUID
    role: str
    factory_id: uuid.UUID | None
    factory_code: str | None


class MembershipOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    roles: list[RoleAssignmentOut]


class RoleGrantCreate(BaseModel):
    role: str
    factory_id: uuid.UUID | None = None

    @field_validator("role")
    @classmethod
    def _role_known(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError(f"role must be one of {sorted(ROLES)}")
        return value


class PolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    version_no: int
    is_demo: bool
    status: str
    rules: dict[str, Any]
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime


class SettingsOut(BaseModel):
    model_calls_limit: int
    token_budget: int
    run_deadline_seconds: int
    max_tool_calls: int
    max_upload_bytes: int
    llm_provider: str
    llm_model: str
