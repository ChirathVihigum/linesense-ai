"""Pure-ASGI middleware for request trace IDs."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

_REQUEST_ID_HEADER = b"x-request-id"


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class TraceIdMiddleware:
    """Assigns a trace ID to every HTTP request.

    Accepts an inbound ``X-Request-Id`` header only if it parses as a UUID;
    otherwise generates a new one. The trace ID is exposed via
    ``trace_id_var`` (for logging) and ``scope["state"]["trace_id"]``, and is
    echoed back on the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        inbound_raw = headers.get(_REQUEST_ID_HEADER)
        inbound = inbound_raw.decode("latin-1") if inbound_raw is not None else None
        trace_id = inbound if inbound and _is_uuid(inbound) else uuid.uuid4().hex

        state: dict[str, Any] = scope.setdefault("state", {})
        state["trace_id"] = trace_id
        token = trace_id_var.set(trace_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = message.setdefault("headers", [])
                response_headers.append((_REQUEST_ID_HEADER, trace_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            trace_id_var.reset(token)


_BASE_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
)
_NO_STORE_PREFIXES = ("/api", "/auth")


def security_headers_for(path: str) -> dict[str, str]:
    """The security headers every response to ``path`` carries."""
    headers = {
        name.decode("latin-1"): value.decode("latin-1") for name, value in _BASE_SECURITY_HEADERS
    }
    if path.startswith(_NO_STORE_PREFIXES):
        headers["cache-control"] = "no-store"
    return headers


class SecurityHeadersMiddleware:
    """Adds ``X-Content-Type-Options``, ``Referrer-Policy`` and ``X-Frame-Options``
    to every HTTP response, and ``Cache-Control: no-store`` under ``/api`` and
    ``/auth`` (replacing any value set by the handler)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in security_headers_for(scope["path"]).items()
        ]
        names = {name for name, _ in extra}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in names
                ]
                message["headers"] = existing + extra
            await send(message)

        await self.app(scope, receive, send_wrapper)
