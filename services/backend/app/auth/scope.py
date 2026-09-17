"""Organization/factory scoping for resources (backend-contracts.md section 4).

Visibility rules:

* A row in another organization, or in a factory where the principal has no
  role at all, is *inaccessible* and yields 404 ``NOT_FOUND`` (existence is
  never revealed).
* A row in an accessible factory for which the principal lacks the
  requested permission yields 403 ``FORBIDDEN``.
* Organization-level rows (no ``factory_id``, e.g. materials or styles) are
  accessible to any member with a role; the permission must be granted by
  at least one of the principal's roles.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.auth.policy import PERMISSIONS, Principal
from app.db.base import Base
from app.db.models import Factory


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Resource not found.")


def _forbidden(permission: str) -> AppError:
    return AppError(403, "FORBIDDEN", f"Missing permission {permission}.")


async def load_scoped[ModelT: Base](
    session: AsyncSession,
    model: type[ModelT],
    resource_id: Any,
    principal: Principal,
    permission: str,
) -> ModelT:
    """Load ``model`` by primary key, enforcing organization and factory scope."""
    allowed = PERMISSIONS[permission]  # KeyError on an unknown permission
    row = await session.get(model, resource_id)
    if row is None or getattr(row, "organization_id", None) != principal.organization_id:
        raise _not_found()

    factory_id: uuid.UUID | None = (
        row.id if isinstance(row, Factory) else getattr(row, "factory_id", None)
    )
    if factory_id is None:
        if not any(principal.roles_by_factory.values()):
            raise _not_found()
        if not principal.has_anywhere(permission):
            raise _forbidden(permission)
        return row

    roles = principal.roles_for(factory_id)
    if not roles:
        raise _not_found()
    if not roles & allowed:
        raise _forbidden(permission)
    return row


async def accessible_factory_ids(
    session: AsyncSession, principal: Principal, permission: str
) -> list[uuid.UUID]:
    """Ids of the principal's organization's factories where ``permission`` is granted."""
    allowed = PERMISSIONS[permission]
    stmt = (
        select(Factory.id)
        .where(Factory.organization_id == principal.organization_id)
        .order_by(Factory.code)
    )
    if not principal.roles_by_factory.get(None, frozenset()) & allowed:
        explicit = [
            factory_id
            for factory_id, roles in principal.roles_by_factory.items()
            if factory_id is not None and roles & allowed
        ]
        if not explicit:
            return []
        stmt = stmt.where(Factory.id.in_(explicit))
    return list((await session.scalars(stmt)).all())


async def visible_factories(session: AsyncSession, principal: Principal) -> list[Factory]:
    """Factories of the principal's organization where it holds any role, by code."""
    stmt = (
        select(Factory)
        .where(Factory.organization_id == principal.organization_id)
        .order_by(Factory.code)
    )
    if not principal.has_org_wide_roles:
        explicit = list(principal.factory_ids())
        if not explicit:
            return []
        stmt = stmt.where(Factory.id.in_(explicit))
    return list((await session.scalars(stmt)).all())
