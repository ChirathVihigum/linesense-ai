"""Shared builders for the inventory/capacity integration tests (task-8-brief.md)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.policy import Principal
from app.db.models import (
    BomLine,
    BomVersion,
    Factory,
    Material,
    Membership,
    Order,
    Organization,
    RoleAssignment,
    User,
)
from app.domain.vocab import Role
from tests.factories import make_material, make_order, make_style_with_operations


def principal_for(
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    roles_by_factory: dict[uuid.UUID | None, frozenset[str]],
) -> Principal:
    return Principal(
        user_id=user_id,
        organization_id=organization_id,
        session_id=uuid.uuid4(),
        csrf_token="test-csrf",  # noqa: S106 - not a credential
        display_name="Test principal",
        roles_by_factory=roles_by_factory,
    )


async def storekeeper_principal(
    session: AsyncSession, organization: Organization, factory: Factory
) -> Principal:
    user = await session.scalar(select(User).where(User.email == "storekeeper@demo.test"))
    assert user is not None
    return principal_for(user.id, organization.id, {factory.id: frozenset({Role.STOREKEEPER})})


async def make_material_order(
    session: AsyncSession,
    organization: Organization,
    factory: Factory,
    *,
    material: Material | None = None,
    quantity: int = 100,
    quantity_per_unit: Decimal = Decimal("1"),
    wastage_fraction: Decimal = Decimal("0"),
    **order_overrides: Any,
) -> tuple[Material, Order]:
    """An order whose active BOM uses ``material`` (created when omitted)."""
    material = material or await make_material(session, organization=organization)
    style = await make_style_with_operations(session, organization=organization)
    bom_version = BomVersion(style_id=style.id, version_no=1, is_active=True)
    session.add(bom_version)
    await session.flush()
    session.add(
        BomLine(
            bom_version_id=bom_version.id,
            material_id=material.id,
            quantity_per_unit=quantity_per_unit,
            unit=material.unit,
            wastage_fraction=wastage_fraction,
        )
    )
    await session.flush()
    order = await make_order(
        session,
        organization=organization,
        factory=factory,
        style=style,
        bom_version=bom_version,
        quantity=quantity,
        **order_overrides,
    )
    return material, order


async def grant_role(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID | None,
    role: str,
) -> None:
    async with session_factory() as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.organization_id == organization_id, Membership.user_id == user_id
            )
        )
        if membership is None:
            membership = Membership(organization_id=organization_id, user_id=user_id)
            session.add(membership)
            await session.flush()
        session.add(RoleAssignment(membership_id=membership.id, factory_id=factory_id, role=role))
        await session.commit()
