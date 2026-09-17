"""Opaque server-side sessions (``sessions`` table) and principal loading.

The browser cookie ``ls_session`` holds a high-entropy random token; only its
SHA-256 hex digest is stored. Sessions have an absolute lifetime
(``LS_SESSION_MAX_AGE_SECONDS``, 8 hours by default), are rotated at login and
revoked at logout.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.policy import Principal
from app.db.models import Membership, RoleAssignment, SessionRecord, User
from app.settings import get_settings

SESSION_COOKIE = "ls_session"
LAST_SEEN_RESOLUTION = timedelta(minutes=1)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_session(
    session: AsyncSession, user_id: uuid.UUID, *, max_age_seconds: int | None = None
) -> tuple[str, SessionRecord]:
    """Create a session row; returns the raw cookie token and the record (flushed)."""
    lifetime = (
        max_age_seconds if max_age_seconds is not None else get_settings().session_max_age_seconds
    )
    raw_token = secrets.token_urlsafe(32)
    now = _now()
    record = SessionRecord(
        token_hash=hash_token(raw_token),
        user_id=user_id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + timedelta(seconds=lifetime),
        last_seen_at=now,
    )
    session.add(record)
    await session.flush()
    return raw_token, record


async def resolve_session(session: AsyncSession, raw_token: str | None) -> SessionRecord | None:
    """Return the live session for ``raw_token``; ``None`` if unknown, expired or revoked.

    ``last_seen_at`` is refreshed at most once per minute (the caller commits).
    """
    if not raw_token:
        return None
    record = await session.scalar(
        select(SessionRecord).where(SessionRecord.token_hash == hash_token(raw_token))
    )
    now = _now()
    if record is None or record.revoked_at is not None or record.expires_at <= now:
        return None
    if now - record.last_seen_at >= LAST_SEEN_RESOLUTION:
        record.last_seen_at = now
        await session.flush()
    return record


async def revoke_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    await session.execute(
        update(SessionRecord)
        .where(SessionRecord.id == session_id, SessionRecord.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def revoke_session_token(session: AsyncSession, raw_token: str | None) -> None:
    """Revoke whatever session ``raw_token`` names (used for rotation at login)."""
    if not raw_token:
        return
    await session.execute(
        update(SessionRecord)
        .where(
            SessionRecord.token_hash == hash_token(raw_token),
            SessionRecord.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )


async def active_membership(session: AsyncSession, user_id: uuid.UUID) -> Membership | None:
    """The user's active membership (oldest first if, unusually, there are several)."""
    result = await session.scalars(
        select(Membership)
        .where(Membership.user_id == user_id, Membership.is_active.is_(True))
        .order_by(Membership.created_at, Membership.id)
        .limit(1)
    )
    return result.first()


async def load_principal(session: AsyncSession, record: SessionRecord) -> Principal | None:
    """Build the ``Principal`` for a live session.

    Returns ``None`` when the user is inactive or has no active membership.
    """
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    membership = await active_membership(session, user.id)
    if membership is None:
        return None
    rows = await session.execute(
        select(RoleAssignment.factory_id, RoleAssignment.role).where(
            RoleAssignment.membership_id == membership.id
        )
    )
    grouped: defaultdict[uuid.UUID | None, set[str]] = defaultdict(set)
    for factory_id, role in rows:
        grouped[factory_id].add(role)
    return Principal(
        user_id=user.id,
        organization_id=membership.organization_id,
        session_id=record.id,
        csrf_token=record.csrf_token,
        display_name=user.display_name,
        roles_by_factory={key: frozenset(roles) for key, roles in grouped.items()},
    )
