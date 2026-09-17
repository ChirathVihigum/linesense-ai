"""Table-driven checks of the role/permission matrix (backend-contracts.md section 4)."""

from __future__ import annotations

import uuid

import pytest

from app.api.errors import AppError
from app.auth.policy import PERMISSIONS, ROLES, Principal, require

ALL_ROLES = {
    "org_admin",
    "supervisor",
    "planner",
    "storekeeper",
    "ie_engineer",
    "quality_manager",
    "viewer",
}

# Transcribed independently from the contract table so a typo in the policy
# module cannot silently agree with itself.
EXPECTED: dict[str, set[str]] = {
    "order:read": ALL_ROLES,
    "analysis:read": ALL_ROLES,
    "inventory:read": ALL_ROLES,
    "capacity:read": ALL_ROLES,
    "ie:read": ALL_ROLES,
    "quality:read": ALL_ROLES,
    "document:read": ALL_ROLES,
    "order:create": {"planner", "supervisor"},
    "order:import": {"planner", "supervisor"},
    "order:transition": {"planner", "supervisor"},
    "order:dispatch": {"supervisor"},
    "order:cancel": {"supervisor"},
    "analysis:run": {"planner", "supervisor"},
    "recommendation:decide": {"supervisor"},
    "recommendation:apply": {"supervisor"},
    "inventory:write": {"storekeeper"},
    "ie:write": {"ie_engineer"},
    "quality:inspect": {"quality_manager"},
    "quality:hold": {"quality_manager"},
    "quality:release": {"quality_manager"},
    "document:upload": {"org_admin", "supervisor", "quality_manager", "ie_engineer"},
    "note:create": {"supervisor", "planner", "storekeeper", "ie_engineer", "quality_manager"},
    "audit:read": {"org_admin", "supervisor"},
    "admin:manage": {"org_admin"},
}

FACTORY_A = uuid.uuid4()
FACTORY_B = uuid.uuid4()


def _principal(roles_by_factory: dict[uuid.UUID | None, frozenset[str]]) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        csrf_token="csrf",
        display_name="Test",
        roles_by_factory=roles_by_factory,
    )


def test_roles_and_permission_keys_match_contract() -> None:
    assert set(ROLES) == ALL_ROLES
    assert set(PERMISSIONS) == set(EXPECTED)
    for permission, roles in PERMISSIONS.items():
        assert isinstance(roles, frozenset)
        assert roles == EXPECTED[permission], permission


@pytest.mark.parametrize("role", sorted(ALL_ROLES))
@pytest.mark.parametrize("permission", sorted(EXPECTED))
def test_every_role_permission_pair(role: str, permission: str) -> None:
    principal = _principal({FACTORY_A: frozenset({role})})
    allowed = role in EXPECTED[permission]

    assert principal.has(permission, FACTORY_A) is allowed
    if allowed:
        require(principal, permission, FACTORY_A)
    else:
        with pytest.raises(AppError) as excinfo:
            require(principal, permission, FACTORY_A)
        assert excinfo.value.status_code == 403
        assert excinfo.value.code == "FORBIDDEN"


def test_named_denials() -> None:
    viewer = _principal({FACTORY_A: frozenset({"viewer"})})
    admin = _principal({None: frozenset({"org_admin"})})
    assert not viewer.has("order:create", FACTORY_A)
    assert not admin.has("quality:release", FACTORY_A)
    assert not admin.has("recommendation:decide", FACTORY_A)
    assert admin.has("admin:manage", FACTORY_A)


def test_org_wide_role_applies_to_any_factory() -> None:
    principal = _principal({None: frozenset({"supervisor"})})
    assert principal.has("order:dispatch", FACTORY_A)
    assert principal.has("order:dispatch", FACTORY_B)
    assert principal.roles_for(FACTORY_B) == frozenset({"supervisor"})
    assert principal.factory_ids() == frozenset()


def test_factory_role_does_not_apply_to_another_factory() -> None:
    principal = _principal({FACTORY_A: frozenset({"planner"})})
    assert principal.has("order:create", FACTORY_A)
    assert not principal.has("order:create", FACTORY_B)
    assert not principal.has("order:read", FACTORY_B)
    assert principal.roles_for(FACTORY_B) == frozenset()
    with pytest.raises(AppError):
        require(principal, "order:read", FACTORY_B)


def test_roles_combine_org_wide_and_factory_roles() -> None:
    principal = _principal({None: frozenset({"org_admin"}), FACTORY_A: frozenset({"storekeeper"})})
    assert principal.roles_for(FACTORY_A) == frozenset({"org_admin", "storekeeper"})
    assert principal.factory_ids() == frozenset({FACTORY_A})
    assert principal.has("inventory:write", FACTORY_A)
    assert not principal.has("inventory:write", FACTORY_B)


def test_unknown_permission_is_a_programming_error() -> None:
    principal = _principal({FACTORY_A: frozenset({"supervisor"})})
    with pytest.raises(KeyError):
        principal.has("order:explode", FACTORY_A)
