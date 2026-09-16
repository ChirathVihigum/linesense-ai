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
