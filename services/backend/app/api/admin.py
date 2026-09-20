"""Administration routes: memberships/roles, quality policies, effective
run settings (task-22-brief.md requirement 2).

Every route requires `admin:manage`, granted only to `org_admin`, and is not
factory-scoped (the roster and settings are organization-wide), so
permission is checked with `Principal.has_anywhere` rather than
`app.auth.scope.load_scoped`. Every membership/role change revokes every
active session of the affected user (a privilege change must not leave a
stale session with the old permission set alive) and is audited with
before/after state.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.orders import request_trace_id as _request_trace_id
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.admin import (
    MembershipOut,
    PolicyVersionOut,
    RoleAssignmentOut,
    RoleGrantCreate,
    SettingsOut,
)
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.db.models import (
    Factory,
    Membership,
    QualityPolicyVersion,
    RoleAssignment,
    SessionRecord,
    User,
)
from app.db.session import get_db_session
from app.domain.clock import utcnow
from app.domain.vocab import ActorType, AuditOutcome, Role
from app.llm import FixtureLLMClient
from app.orchestration.protocol import TaskConstraints
from app.settings import Settings

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

ADMIN_PERMISSION = "admin:manage"
# Match the `analysis_runs` column defaults (backend-contracts.md "Workflow"):
# every run is created with these limits unless a future task makes them
# per-organization configuration.
DEFAULT_MODEL_CALLS_LIMIT = 12
DEFAULT_TOKEN_BUDGET = 200_000


def _require_admin(principal: Principal) -> None:
    if not principal.has_anywhere(ADMIN_PERMISSION):
        raise AppError(403, "FORBIDDEN", f"Missing permission {ADMIN_PERMISSION}.")


async def _audit_denial(
    request: Request, principal: Principal, exc: AppError, *, action: str, target_id: str
) -> None:
    await audit_denial_from_error(
        request,
        principal,
        exc,
        factory_id=None,
        action=action,
        target_type="membership",
        target_id=target_id,
    )


async def _load_membership(
    session: AsyncSession, principal: Principal, membership_id: uuid.UUID
) -> Membership:
    membership = await session.get(Membership, membership_id)
    if membership is None or membership.organization_id != principal.organization_id:
        raise AppError(404, "NOT_FOUND", "Resource not found.")
    return membership


async def _membership_out(session: AsyncSession, membership: Membership) -> MembershipOut:
    user = await session.get(User, membership.user_id)
    if user is None:
        raise RuntimeError("a membership always references an existing user")
    rows = (
        await session.execute(
            select(RoleAssignment, Factory.code)
            .outerjoin(Factory, Factory.id == RoleAssignment.factory_id)
            .where(RoleAssignment.membership_id == membership.id)
            .order_by(RoleAssignment.role, Factory.code)
        )
    ).all()
    return MembershipOut(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=membership.is_active,
        roles=[
            RoleAssignmentOut(
                id=row.id, role=row.role, factory_id=row.factory_id, factory_code=code
            )
            for row, code in rows
        ],
    )


async def _revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every live session of `user_id` (privilege-change rotation)."""
    await session.execute(
        sa.update(SessionRecord)
        .where(SessionRecord.user_id == user_id, SessionRecord.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def _active_org_admin_count(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    excluding_assignment_id: uuid.UUID | None = None,
) -> int:
    conditions: list[Any] = [
        Membership.organization_id == organization_id,
        Membership.is_active.is_(True),
        RoleAssignment.role == Role.ORG_ADMIN.value,
    ]
    if excluding_assignment_id is not None:
        conditions.append(RoleAssignment.id != excluding_assignment_id)
    count = await session.scalar(
        select(sa.func.count())
        .select_from(RoleAssignment)
        .join(Membership, Membership.id == RoleAssignment.membership_id)
        .where(*conditions)
    )
    return int(count or 0)


async def idempotent_command(
    session: AsyncSession,
    principal: Principal,
    *,
    operation: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
    status_code: int,
    command: Any,
) -> Any:
    """Idempotency wrapper shared by every admin write (mirrors
    `app.api.inventory._run_command`, without the audit-denial hook since
    each admin command audits its own denial before running)."""
    early = await idempotent_start(
        session,
        principal,
        operation=operation,
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early
    body = await command()
    return await idempotent_finish(
        session,
        principal,
        operation=operation,
        key=idempotency_key,
        status_code=status_code,
        body=body,
    )


@router.get("/memberships", response_model=Page[MembershipOut])
async def list_memberships(
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[MembershipOut]:
    _require_admin(principal)
    base = (
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == principal.organization_id)
    )
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    memberships = (
        await session.scalars(base.order_by(User.email).limit(page.limit).offset(page.offset))
    ).all()
    items = [await _membership_out(session, membership) for membership in memberships]
    return Page[MembershipOut](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )


@router.post("/memberships/{membership_id}/roles", response_model=MembershipOut, status_code=201)
async def grant_role(
    membership_id: uuid.UUID,
    body: RoleGrantCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> MembershipOut:
        try:
            _require_admin(principal)
        except AppError as exc:
            await _audit_denial(
                request, principal, exc, action="admin.role_grant", target_id=str(membership_id)
            )
            raise
        membership = await _load_membership(session, principal, membership_id)

        if body.factory_id is not None:
            factory = await session.get(Factory, body.factory_id)
            if factory is None or factory.organization_id != principal.organization_id:
                raise AppError(404, "NOT_FOUND", "Resource not found.")

        existing = await session.scalar(
            select(RoleAssignment).where(
                RoleAssignment.membership_id == membership.id,
                RoleAssignment.role == body.role,
                RoleAssignment.factory_id.is_(None)
                if body.factory_id is None
                else RoleAssignment.factory_id == body.factory_id,
            )
        )
        if existing is None:
            assignment = RoleAssignment(
                membership_id=membership.id, factory_id=body.factory_id, role=body.role
            )
            session.add(assignment)
            await session.flush()
            await record_audit(
                session,
                organization_id=principal.organization_id,
                factory_id=body.factory_id,
                actor_type=ActorType.USER.value,
                actor_id=str(principal.user_id),
                action="admin.role_grant",
                target_type="role_assignment",
                target_id=str(assignment.id),
                outcome=AuditOutcome.SUCCESS.value,
                trace_id=_request_trace_id(request),
                before=None,
                after={
                    "membership_id": str(membership.id),
                    "role": body.role,
                    "factory_id": str(body.factory_id) if body.factory_id else None,
                },
            )
            await _revoke_all_sessions(session, membership.user_id)
        return await _membership_out(session, membership)

    return await idempotent_command(
        session,
        principal,
        operation="admin:role_grant",
        idempotency_key=idempotency_key,
        request_payload={"membership_id": str(membership_id), **body.model_dump(mode="json")},
        status_code=201,
        command=command,
    )


@router.delete("/role-assignments/{role_assignment_id}", response_model=MembershipOut)
async def revoke_role(
    role_assignment_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> MembershipOut:
        try:
            _require_admin(principal)
        except AppError as exc:
            await _audit_denial(
                request,
                principal,
                exc,
                action="admin.role_revoke",
                target_id=str(role_assignment_id),
            )
            raise
        assignment = await session.scalar(
            select(RoleAssignment)
            .join(Membership, Membership.id == RoleAssignment.membership_id)
            .where(
                RoleAssignment.id == role_assignment_id,
                Membership.organization_id == principal.organization_id,
            )
        )
        if assignment is None:
            raise AppError(404, "NOT_FOUND", "Resource not found.")
        membership = await _load_membership(session, principal, assignment.membership_id)

        if assignment.role == Role.ORG_ADMIN.value:
            remaining = await _active_org_admin_count(
                session, principal.organization_id, excluding_assignment_id=assignment.id
            )
            if remaining == 0:
                raise AppError(
                    409, "CONFLICT", "Cannot remove the organization's last org_admin assignment."
                )

        before = {
            "membership_id": str(membership.id),
            "role": assignment.role,
            "factory_id": str(assignment.factory_id) if assignment.factory_id else None,
        }
        await session.delete(assignment)
        await session.flush()
        await record_audit(
            session,
            organization_id=principal.organization_id,
            factory_id=assignment.factory_id,
            actor_type=ActorType.USER.value,
            actor_id=str(principal.user_id),
            action="admin.role_revoke",
            target_type="role_assignment",
            target_id=str(role_assignment_id),
            outcome=AuditOutcome.SUCCESS.value,
            trace_id=_request_trace_id(request),
            before=before,
            after=None,
        )
        await _revoke_all_sessions(session, membership.user_id)
        return await _membership_out(session, membership)

    return await idempotent_command(
        session,
        principal,
        operation="admin:role_revoke",
        idempotency_key=idempotency_key,
        request_payload={"role_assignment_id": str(role_assignment_id)},
        status_code=200,
        command=command,
    )


@router.post("/memberships/{membership_id}/deactivate", response_model=MembershipOut)
async def deactivate_membership(
    membership_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    async def command() -> MembershipOut:
        try:
            _require_admin(principal)
        except AppError as exc:
            await _audit_denial(
                request, principal, exc, action="admin.deactivate", target_id=str(membership_id)
            )
            raise
        membership = await _load_membership(session, principal, membership_id)
        if membership.user_id == principal.user_id:
            raise AppError(409, "CONFLICT", "You cannot deactivate your own membership.")

        if membership.is_active:
            before = {"is_active": True}
            membership.is_active = False
            await session.flush()
            await record_audit(
                session,
                organization_id=principal.organization_id,
                factory_id=None,
                actor_type=ActorType.USER.value,
                actor_id=str(principal.user_id),
                action="admin.deactivate",
                target_type="membership",
                target_id=str(membership.id),
                outcome=AuditOutcome.SUCCESS.value,
                trace_id=_request_trace_id(request),
                before=before,
                after={"is_active": False},
            )
            await _revoke_all_sessions(session, membership.user_id)
        return await _membership_out(session, membership)

    return await idempotent_command(
        session,
        principal,
        operation="admin:deactivate",
        idempotency_key=idempotency_key,
        request_payload={"membership_id": str(membership_id)},
        status_code=200,
        command=command,
    )


@router.get("/policies", response_model=Page[PolicyVersionOut])
async def list_policies(
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[PolicyVersionOut]:
    _require_admin(principal)
    base = select(QualityPolicyVersion).where(
        QualityPolicyVersion.organization_id == principal.organization_id
    )
    total = await session.scalar(select(sa.func.count()).select_from(base.subquery()))
    rows = (
        await session.scalars(
            base.order_by(QualityPolicyVersion.code, QualityPolicyVersion.version_no.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    items = [PolicyVersionOut.model_validate(row) for row in rows]
    return Page[PolicyVersionOut](
        items=items, total=int(total or 0), limit=page.limit, offset=page.offset
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings_view(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> SettingsOut:
    _require_admin(principal)
    settings: Settings = request.app.state.settings
    if settings.llm_provider == "disabled":
        provider, model = "disabled", "disabled"
    elif settings.llm_provider == "fixture":
        provider, model = FixtureLLMClient.provider, FixtureLLMClient.model
    else:
        provider, model = settings.llm_provider, settings.anthropic_model
    return SettingsOut(
        model_calls_limit=DEFAULT_MODEL_CALLS_LIMIT,
        token_budget=DEFAULT_TOKEN_BUDGET,
        run_deadline_seconds=settings.run_deadline_seconds,
        max_tool_calls=TaskConstraints().max_tool_calls,
        max_upload_bytes=settings.max_upload_bytes,
        llm_provider=provider,
        llm_model=model,
    )
