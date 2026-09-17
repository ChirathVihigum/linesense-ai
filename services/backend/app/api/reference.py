"""Org-scoped reference data used when creating/importing orders: customers
and styles. The path's `factory_id` is used only to establish permission
(these tables have no `factory_id` column of their own).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.orders import CustomerOut, StyleOut
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Customer, Factory, Style
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["reference"])


@router.get("/factories/{factory_id}/customers", response_model=Page[CustomerOut])
async def list_customers(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[CustomerOut]:
    await load_scoped(session, Factory, factory_id, principal, "order:read")
    base = select(Customer).where(Customer.organization_id == principal.organization_id)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = (
        await session.scalars(base.order_by(Customer.code).limit(page.limit).offset(page.offset))
    ).all()
    items = [CustomerOut(id=row.id, code=row.code, name=row.name) for row in rows]
    return Page[CustomerOut](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )


@router.get("/factories/{factory_id}/styles", response_model=Page[StyleOut])
async def list_styles(
    factory_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[StyleOut]:
    await load_scoped(session, Factory, factory_id, principal, "order:read")
    base = select(Style).where(Style.organization_id == principal.organization_id)
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = (
        await session.scalars(base.order_by(Style.code).limit(page.limit).offset(page.offset))
    ).all()
    items = [
        StyleOut(id=row.id, code=row.code, name=row.name, product_type=row.product_type)
        for row in rows
    ]
    return Page[StyleOut](items=items, total=int(total or 0), limit=page.limit, offset=page.offset)
