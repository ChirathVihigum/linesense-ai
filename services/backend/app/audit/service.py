"""Audit event recording (backend-contracts.md section 5).

``record_audit`` writes inside the caller's transaction, so the event commits
or rolls back together with the command it describes. ``audit_denied`` uses
its own short transaction because a denied request's transaction is rolled
back by the error that denies it.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.middleware import trace_id_var
from app.db.models import AuditEvent
from app.domain.vocab import ActorType, AuditOutcome

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(r"token|password|secret", re.IGNORECASE)


def redact(value: Any) -> Any:
    """Recursively replace values whose key names look sensitive."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


async def record_audit(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID | None,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    outcome: str,
    reason: str | None = None,
    trace_id: str | None = None,
    run_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit event to ``session`` and flush it (the caller commits)."""
    event = AuditEvent(
        organization_id=organization_id,
        factory_id=factory_id,
        actor_type=ActorType(actor_type).value,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=AuditOutcome(outcome).value,
        reason=reason,
        trace_id=trace_id if trace_id is not None else (trace_id_var.get() or None),
        run_id=run_id,
        before=redact(before) if before is not None else None,
        after=redact(after) if after is not None else None,
    )
    session.add(event)
    await session.flush()
    return event


async def audit_denied(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: uuid.UUID,
    factory_id: uuid.UUID | None,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    reason: str,
    actor_type: str = ActorType.USER.value,
    trace_id: str | None = None,
    run_id: uuid.UUID | None = None,
) -> None:
    """Record a ``DENIED`` event in its own committed transaction."""
    async with session_factory() as session:
        await record_audit(
            session,
            organization_id=organization_id,
            factory_id=factory_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=AuditOutcome.DENIED.value,
            reason=reason,
            trace_id=trace_id,
            run_id=run_id,
        )
        await session.commit()
