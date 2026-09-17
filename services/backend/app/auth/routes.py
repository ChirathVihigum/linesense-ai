"""``/auth/login``, ``/auth/callback`` and ``/auth/logout``.

The OIDC handshake state (Authlib's state, nonce and PKCE verifier, plus the
post-login ``next`` path) lives in the short-lived signed ``ls_oidc`` cookie
managed by Starlette's ``SessionMiddleware``. Provider tokens are used only
to establish the identity and are never stored or sent to the browser.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import httpx
import structlog
from authlib.common.errors import AuthlibBaseError
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from joserfc.errors import JoseError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_session_record
from app.api.errors import AppError
from app.audit.service import record_audit
from app.auth.oidc import oidc_client
from app.auth.sessions import (
    SESSION_COOKIE,
    active_membership,
    create_session,
    load_principal,
    revoke_session,
    revoke_session_token,
)
from app.db.models import User
from app.db.session import get_db_session
from app.domain.vocab import ActorType, AuditOutcome
from app.settings import Settings

logger = structlog.get_logger("app.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

NEXT_KEY = "ls_next"
LOGIN_FAILED_PATH = "/login?error=auth_failed"
_CALLBACK_ERRORS = (AuthlibBaseError, JoseError, httpx.HTTPError, ValueError, KeyError)


def safe_next_path(value: str | None) -> str:
    """Accept only same-origin relative paths (a single leading ``/``)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _absolute(settings: Settings, path: str) -> str:
    return settings.public_origin.rstrip("/") + path


def _set_session_cookie(response: Response, settings: Settings, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=settings.session_max_age_seconds,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
    )


@router.get("/login")
async def login(request: Request, next: str | None = Query(default=None)) -> Response:  # noqa: A002
    settings = _settings(request)
    request.session[NEXT_KEY] = safe_next_path(next)
    try:
        response: Response = await oidc_client(request).authorize_redirect(
            request, settings.oidc_redirect_uri
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("oidc_discovery_failed", error=type(exc).__name__)
        raise AppError(503, "SERVICE_UNAVAILABLE", "The identity provider is unavailable.") from exc
    return response


async def _audit_login(
    session: AsyncSession,
    user_id: uuid.UUID,
    outcome: AuditOutcome,
    reason: str | None = None,
) -> None:
    membership = await active_membership(session, user_id)
    if membership is None:
        logger.info(
            "auth_login_not_audited_no_membership", user_id=str(user_id), outcome=outcome.value
        )
        return
    await record_audit(
        session,
        organization_id=membership.organization_id,
        factory_id=None,
        actor_type=ActorType.USER.value,
        actor_id=str(user_id),
        action="auth.login",
        target_type="user",
        target_id=str(user_id),
        outcome=outcome.value,
        reason=reason,
    )


@router.get("/callback")
async def callback(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> RedirectResponse:
    settings = _settings(request)
    next_path = safe_next_path(request.session.pop(NEXT_KEY, None))
    failure = RedirectResponse(_absolute(settings, LOGIN_FAILED_PATH), status_code=302)

    try:
        token = await oidc_client(request).authorize_access_token(request)
        claims = token["userinfo"]
        issuer = str(claims["iss"])
        subject = str(claims["sub"])
        email = str(claims["email"])
        display_name = str(claims.get("name") or email)
    except _CALLBACK_ERRORS as exc:
        logger.warning("oidc_callback_failed", error=type(exc).__name__)
        return failure

    row = (
        await session.execute(
            insert(User)
            .values(
                id=uuid.uuid4(),
                issuer=issuer,
                subject=subject,
                email=email,
                display_name=display_name,
            )
            .on_conflict_do_update(
                index_elements=["issuer", "subject"],
                set_={"email": email, "display_name": display_name},
            )
            .returning(User.id, User.is_active)
        )
    ).one()
    user_id, is_active = row

    if not is_active:
        await _audit_login(session, user_id, AuditOutcome.FAILED, "user is inactive")
        logger.warning("oidc_login_inactive_user", user_id=str(user_id))
        return failure

    # Session rotation: whatever session this browser held is revoked first.
    await revoke_session_token(session, request.cookies.get(SESSION_COOKIE))
    raw_token, _record = await create_session(
        session, user_id, max_age_seconds=settings.session_max_age_seconds
    )
    await _audit_login(session, user_id, AuditOutcome.SUCCESS)

    response = RedirectResponse(_absolute(settings, next_path), status_code=302)
    _set_session_cookie(response, settings, raw_token)
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    settings = _settings(request)
    record = await current_session_record(request, session)
    if record is not None:
        principal = await load_principal(session, record)
        await revoke_session(session, record.id)
        if principal is not None:
            await record_audit(
                session,
                organization_id=principal.organization_id,
                factory_id=None,
                actor_type=ActorType.USER.value,
                actor_id=str(principal.user_id),
                action="auth.logout",
                target_type="user",
                target_id=str(principal.user_id),
                outcome=AuditOutcome.SUCCESS.value,
            )
        else:
            logger.info("auth_logout_not_audited_no_membership", user_id=str(record.user_id))
    response = Response(status_code=204)
    _clear_session_cookie(response, settings)
    return response
