"""``GET /api/v1/me`` — the signed-in user, their organization, factories and CSRF token."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal
from app.auth.policy import Principal, permissions_for_roles
from app.auth.scope import visible_factories
from app.db.models import Organization, User
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["me"])


class MeUser(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class MeOrganization(BaseModel):
    id: uuid.UUID
    name: str


class MeFactory(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    timezone: str
    roles: list[str]
    permissions: list[str]


class MeResponse(BaseModel):
    user: MeUser
    organization: MeOrganization
    factories: list[MeFactory]
    csrf_token: str


@router.get("/me", response_model=MeResponse)
async def get_me(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    user = await session.get(User, principal.user_id)
    organization = await session.get(Organization, principal.organization_id)
    if user is None or organization is None:
        raise RuntimeError("principal refers to a missing user or organization")
    factories = []
    for factory in await visible_factories(session, principal):
        roles = principal.roles_for(factory.id)
        factories.append(
            MeFactory(
                id=factory.id,
                code=factory.code,
                name=factory.name,
                timezone=factory.timezone,
                roles=sorted(roles),
                permissions=permissions_for_roles(roles),
            )
        )
    return MeResponse(
        user=MeUser(id=user.id, email=user.email, display_name=user.display_name),
        organization=MeOrganization(id=organization.id, name=organization.name),
        factories=factories,
        csrf_token=principal.csrf_token,
    )
