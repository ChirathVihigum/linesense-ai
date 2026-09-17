"""Roles, permissions and the authenticated principal (backend-contracts.md section 4)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.api.errors import AppError
from app.domain.vocab import ROLES, Role

__all__ = ["PERMISSIONS", "ROLES", "Principal", "require"]

_ALL_ROLES = frozenset(ROLES)
_READ_PERMISSIONS = (
    "order:read",
    "analysis:read",
    "inventory:read",
    "capacity:read",
    "ie:read",
    "quality:read",
    "document:read",
)


def _roles(*roles: Role) -> frozenset[str]:
    return frozenset(role.value for role in roles)


_PERMISSION_TABLE: dict[str, frozenset[str]] = {
    **{permission: _ALL_ROLES for permission in _READ_PERMISSIONS},
    "order:create": _roles(Role.PLANNER, Role.SUPERVISOR),
    "order:import": _roles(Role.PLANNER, Role.SUPERVISOR),
    "order:transition": _roles(Role.PLANNER, Role.SUPERVISOR),
    "order:dispatch": _roles(Role.SUPERVISOR),
    "order:cancel": _roles(Role.SUPERVISOR),
    "analysis:run": _roles(Role.PLANNER, Role.SUPERVISOR),
    "recommendation:decide": _roles(Role.SUPERVISOR),
    "recommendation:apply": _roles(Role.SUPERVISOR),
    "inventory:write": _roles(Role.STOREKEEPER),
    "ie:write": _roles(Role.IE_ENGINEER),
    "quality:inspect": _roles(Role.QUALITY_MANAGER),
    "quality:hold": _roles(Role.QUALITY_MANAGER),
    "quality:release": _roles(Role.QUALITY_MANAGER),
    "document:upload": _roles(
        Role.ORG_ADMIN, Role.SUPERVISOR, Role.QUALITY_MANAGER, Role.IE_ENGINEER
    ),
    "note:create": _roles(
        Role.SUPERVISOR, Role.PLANNER, Role.STOREKEEPER, Role.IE_ENGINEER, Role.QUALITY_MANAGER
    ),
    "audit:read": _roles(Role.ORG_ADMIN, Role.SUPERVISOR),
    "admin:manage": _roles(Role.ORG_ADMIN),
}

PERMISSIONS: Mapping[str, frozenset[str]] = MappingProxyType(_PERMISSION_TABLE)
"""Permission -> the roles that grant it."""


def permissions_for_roles(roles: frozenset[str]) -> list[str]:
    """Sorted permissions granted by any of ``roles``."""
    return sorted(p for p, allowed in PERMISSIONS.items() if roles & allowed)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    csrf_token: str
    display_name: str
    roles_by_factory: Mapping[uuid.UUID | None, frozenset[str]]  # None key = org-wide roles

    def roles_for(self, factory_id: uuid.UUID) -> frozenset[str]:
        empty: frozenset[str] = frozenset()
        return self.roles_by_factory.get(factory_id, empty) | self.roles_by_factory.get(None, empty)

    def has(self, permission: str, factory_id: uuid.UUID) -> bool:
        """Whether any role for ``factory_id`` grants ``permission``.

        Raises ``KeyError`` for a permission name that is not in the policy,
        so a typo can never silently deny (or allow) everything.
        """
        return bool(self.roles_for(factory_id) & PERMISSIONS[permission])

    def has_anywhere(self, permission: str) -> bool:
        """Whether any factory (or org-wide) role grants ``permission``."""
        allowed = PERMISSIONS[permission]
        return any(roles & allowed for roles in self.roles_by_factory.values())

    def factory_ids(self) -> frozenset[uuid.UUID]:
        """Factories with an explicit role; org-wide roles are expanded via the DB
        (see ``app.auth.scope.accessible_factory_ids``)."""
        return frozenset(
            key for key, roles in self.roles_by_factory.items() if key is not None and roles
        )

    @property
    def has_org_wide_roles(self) -> bool:
        return bool(self.roles_by_factory.get(None))


def require(principal: Principal, permission: str, factory_id: uuid.UUID) -> None:
    """Raise 403 ``FORBIDDEN`` unless ``principal`` has ``permission`` for ``factory_id``.

    Pure check: the caller must already have established that ``factory_id``
    belongs to ``principal.organization_id`` (``app.auth.scope``).
    """
    if not principal.has(permission, factory_id):
        raise AppError(403, "FORBIDDEN", f"Missing permission {permission}.")
