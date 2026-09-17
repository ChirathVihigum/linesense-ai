"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.audit import router as audit_router
from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.imports import router as imports_router
from app.api.me import router as me_router
from app.api.middleware import SecurityHeadersMiddleware, TraceIdMiddleware
from app.api.notifications import router as notifications_router
from app.api.orders import router as orders_router
from app.api.reference import router as reference_router
from app.auth.csrf import CsrfMiddleware
from app.auth.oidc import build_oauth
from app.auth.routes import router as auth_router
from app.logging import configure_logging
from app.settings import Settings, get_settings

OIDC_HANDSHAKE_COOKIE = "ls_oidc"
OIDC_HANDSHAKE_MAX_AGE_SECONDS = 600


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)

    app = FastAPI(title="LineSense AI", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.oauth = build_oauth(resolved_settings)

    # Starlette runs the most recently added middleware first, so the
    # effective order (outermost first) is: trace id -> security headers ->
    # OIDC handshake cookie -> CSRF -> routes.
    app.add_middleware(CsrfMiddleware, settings=resolved_settings)
    # The signed session cookie is used only for the OIDC handshake (state,
    # nonce, PKCE verifier, post-login path); application sessions are the
    # opaque server-side `ls_session` records.
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolved_settings.session_secret.get_secret_value(),
        session_cookie=OIDC_HANDSHAKE_COOKIE,
        max_age=OIDC_HANDSHAKE_MAX_AGE_SECONDS,
        path="/auth",
        same_site="lax",
        https_only=resolved_settings.environment == "production",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TraceIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(orders_router)
    app.include_router(imports_router)
    app.include_router(audit_router)
    app.include_router(notifications_router)
    app.include_router(reference_router)

    return app
