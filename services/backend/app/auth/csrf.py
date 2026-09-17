"""CSRF protection for cookie-authenticated unsafe requests (pure ASGI).

For every non-GET/HEAD/OPTIONS request to ``/api/...`` or ``/auth/logout``:

1. The request's origin — the ``Origin`` header, or the origin of ``Referer``
   when ``Origin`` is absent — must equal ``LS_PUBLIC_ORIGIN`` exactly.
2. If the ``ls_session`` cookie names a live session, ``X-CSRF-Token`` must
   equal that session's ``csrf_token`` (constant-time comparison).

A request without a live session carries no ambient authority, so it is
passed on and the route's authentication dependency answers 401. The
resolved session is stashed on the ASGI scope state so ``app.api.deps`` does
not resolve it twice. ``/internal/`` routes use bearer service tokens, not
cookies, and are not handled here.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import structlog
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import error_response
from app.auth.sessions import SESSION_COOKIE, resolve_session
from app.db.session import get_session_factory
from app.settings import Settings

logger = structlog.get_logger("app.auth.csrf")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = b"x-csrf-token"
STATE_RESOLVED = "session_resolved"
STATE_RECORD = "session_record"


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def request_origin(headers: Iterable[tuple[bytes, bytes]]) -> str | None:
    """The request's origin from ``Origin`` (or, if absent, ``Referer``)."""
    values: dict[bytes, str] = {}
    for name, value in headers:
        values.setdefault(name.lower(), value.decode("latin-1"))
    origin = values.get(b"origin")
    if origin is not None:
        parsed = _origin_of(origin)
        return parsed if parsed == origin else None
    referer = values.get(b"referer")
    return _origin_of(referer) if referer else None


def is_protected_path(path: str) -> bool:
    return path.startswith("/api/") or path == "/auth/logout"


class CsrfMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.expected_origin = settings.public_origin.rstrip("/")
        self.database_url = settings.database_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in SAFE_METHODS
            or not is_protected_path(scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = scope.setdefault("state", {})
        headers: list[tuple[bytes, bytes]] = scope.get("headers") or []

        if request_origin(headers) != self.expected_origin:
            await self._reject(scope, receive, send, "origin mismatch")
            return

        raw_token = self._session_cookie(headers)
        record = None
        if raw_token:
            async with get_session_factory(self.database_url)() as session:
                record = await resolve_session(session, raw_token)
                await session.commit()
        state[STATE_RESOLVED] = True
        state[STATE_RECORD] = record

        if record is not None:
            presented = next((v for k, v in headers if k.lower() == CSRF_HEADER), b"")
            if not presented or not hmac.compare_digest(
                presented, record.csrf_token.encode("utf-8")
            ):
                await self._reject(scope, receive, send, "token mismatch")
                return

        await self.app(scope, receive, send)

    @staticmethod
    def _session_cookie(headers: list[tuple[bytes, bytes]]) -> str | None:
        for name, value in headers:
            if name.lower() == b"cookie":
                token = cookie_parser(value.decode("latin-1")).get(SESSION_COOKIE)
                if token:
                    return token
        return None

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, reason: str) -> None:
        trace_id = str(scope.get("state", {}).get("trace_id", ""))
        logger.warning("csrf_rejected", reason=reason, path=scope["path"], trace_id=trace_id)
        response = error_response(403, "CSRF_FAILED", "CSRF validation failed.", trace_id=trace_id)
        await response(scope, receive, send)
