"""Notification read routes: a factory's feed (own + role-targeted) and
marking one read (backend-contracts.md section 2, "notifications").

Notifications have no per-user read tracking: a role-targeted row's
`read_at` is shared by everyone that role reaches, matching the simple
`notifications(read_at)` column in the schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.errors import AppError
from app.api.pagination import Page, PageParams, page_params
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Factory, Notification
from app.db.session import get_db_session
from app.domain import clock

router = APIRouter(prefix="/api/v1", tags=["notifications"])


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    title: str
    body: str
    link: str | None
    created_at: datetime
    read_at: datetime | None


@router.get("/factories/{factory_id}/notifications", response_model=Page[NotificationOut])
async def list_notifications(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[NotificationOut]:
    await load_scoped(session, Factory, factory_id, principal, "order:read")

    roles = principal.roles_for(factory_id)
    role_targeted = (
        sa.and_(Notification.user_id.is_(None), Notification.role.in_(roles))
        if roles
        else sa.false()
    )
    conditions: list[Any] = [
        Notification.factory_id == factory_id,
        sa.or_(Notification.user_id == principal.user_id, role_targeted),
    ]
    base = select(Notification).where(*conditions)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = (
        await session.scalars(
            base.order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    items = [NotificationOut.model_validate(row) for row in rows]
    return Page[NotificationOut](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationOut:
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.organization_id != principal.organization_id:
        raise AppError(404, "NOT_FOUND", "Resource not found.")

    roles = principal.roles_for(notification.factory_id)
    if not roles:
        raise AppError(404, "NOT_FOUND", "Resource not found.")

    visible = notification.user_id == principal.user_id or (
        notification.user_id is None
        and notification.role is not None
        and notification.role in roles
    )
    if not visible:
        # Existence of another user's/role's notification is never revealed,
        # matching every other resource's scoping (404, not 403, for
        # something the caller cannot see at all).
        raise AppError(404, "NOT_FOUND", "Resource not found.")

    if notification.read_at is None:
        notification.read_at = clock.utcnow()
        await session.flush()
    return NotificationOut.model_validate(notification)
