"""`GET /api/v1/factories/{factory_id}/audit-events` (backend-contracts.md section 5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.pagination import Page, PageParams, page_params
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import AuditEvent, Factory
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["audit"])


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: uuid.UUID
    factory_id: uuid.UUID | None
    actor_type: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    outcome: str
    reason: str | None
    trace_id: str | None
    run_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


@router.get("/factories/{factory_id}/audit-events", response_model=Page[AuditEventOut])
async def list_audit_events(
    factory_id: uuid.UUID,
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[AuditEventOut]:
    await load_scoped(session, Factory, factory_id, principal, "audit:read")

    is_org_admin = "org_admin" in principal.roles_by_factory.get(None, frozenset())
    scope_condition = (
        sa.or_(AuditEvent.factory_id == factory_id, AuditEvent.factory_id.is_(None))
        if is_org_admin
        else AuditEvent.factory_id == factory_id
    )
    conditions: list[Any] = [
        AuditEvent.organization_id == principal.organization_id,
        scope_condition,
    ]
    if action is not None:
        conditions.append(AuditEvent.action == action)
    if target_type is not None:
        conditions.append(AuditEvent.target_type == target_type)

    base = select(AuditEvent).where(*conditions)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = (
        await session.scalars(
            base.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    items = [AuditEventOut.model_validate(row) for row in rows]
    return Page[AuditEventOut](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )
