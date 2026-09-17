"""Shared FastAPI dependencies: the authenticated principal and Idempotency-Key."""

from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.auth.csrf import STATE_RECORD, STATE_RESOLVED
from app.auth.policy import Principal
from app.auth.sessions import SESSION_COOKIE, load_principal, resolve_session
from app.db.models import SessionRecord, User
from app.db.session import get_db_session

NO_MEMBERSHIP_MESSAGE = "No LineSense membership"


async def current_session_record(request: Request, session: AsyncSession) -> SessionRecord | None:
    """The live session for this request (reusing the CSRF middleware's lookup)."""
    if getattr(request.state, STATE_RESOLVED, False):
        record: SessionRecord | None = getattr(request.state, STATE_RECORD, None)
        return record
    record = await resolve_session(session, request.cookies.get(SESSION_COOKIE))
    setattr(request.state, STATE_RESOLVED, True)
    setattr(request.state, STATE_RECORD, record)
    return record


async def get_principal(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> Principal:
    """401 ``UNAUTHENTICATED`` without a live session; 403 ``FORBIDDEN`` without membership."""
    if not request.cookies.get(SESSION_COOKIE):
        raise AppError(401, "UNAUTHENTICATED", "Authentication required.")
    record = await current_session_record(request, session)
    if record is None:
        raise AppError(401, "UNAUTHENTICATED", "Authentication required.")
    principal = await load_principal(session, record)
    if principal is None:
        user = await session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise AppError(401, "UNAUTHENTICATED", "Authentication required.")
        raise AppError(403, "FORBIDDEN", NO_MEMBERSHIP_MESSAGE)
    request.state.principal = principal
    return principal


async def get_optional_principal(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> Principal | None:
    """The principal, or ``None`` for anonymous callers and identities without membership."""
    try:
        return await get_principal(request, session)
    except AppError as exc:
        if exc.status_code in (401, 403):
            return None
        raise


async def require_idempotency_key(
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
) -> str:
    """The request's ``Idempotency-Key`` header (422 when missing or of invalid length)."""
    return idempotency_key
