"""The application-wide error contract and its FastAPI exception handlers.

Every non-2xx response follows the shape documented in
``docs/architecture/backend-contracts.md`` section 5::

    {"error": {"code": str, "message": str, "field_errors": [...],
               "trace_id": str, "retry_after_seconds": int | null}}
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import security_headers_for, trace_id_var

logger = structlog.get_logger("app.errors")

_STRIPPED_LOC_PREFIXES = {"body", "query"}

_HTTP_EXCEPTION_CODES: dict[int, str] = {
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


class AppError(Exception):
    """Raise to produce a contract-shaped error response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: list[dict[str, str]] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or []
        self.retry_after_seconds = retry_after_seconds


def _trace_id_for(request: Request) -> str:
    """Resolve the request's trace ID.

    Prefers ``request.state`` (set by ``TraceIdMiddleware`` on the ASGI
    scope) over the ``trace_id_var`` context variable: Starlette runs the
    handler for a bare ``Exception`` from its outermost ``ServerErrorMiddleware``,
    which sits *outside* our middleware, so by the time it runs,
    ``TraceIdMiddleware``'s ``finally`` block has already reset the context
    variable. The scope-backed request state has no such lifetime problem.
    """
    state_trace_id = getattr(request.state, "trace_id", None)
    if state_trace_id:
        return str(state_trace_id)
    return trace_id_var.get()


def _error_body(
    code: str,
    message: str,
    *,
    trace_id: str,
    field_errors: list[dict[str, str]] | None = None,
    retry_after_seconds: int | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
            "trace_id": trace_id,
            "retry_after_seconds": retry_after_seconds,
        }
    }


def _retry_after_headers(retry_after_seconds: int | None) -> dict[str, str] | None:
    if retry_after_seconds is None:
        return None
    return {"Retry-After": str(retry_after_seconds)}


def _strip_loc_prefix(loc: tuple[Any, ...]) -> str:
    parts = list(loc)
    if parts and parts[0] in _STRIPPED_LOC_PREFIXES:
        parts = parts[1:]
    return ".".join(str(part) for part in parts)


def error_response(status_code: int, code: str, message: str, *, trace_id: str) -> JSONResponse:
    """A contract-shaped error response for code outside FastAPI's handlers
    (e.g. pure-ASGI middleware)."""
    return JSONResponse(
        status_code=status_code, content=_error_body(code, message, trace_id=trace_id)
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(
            exc.code,
            exc.message,
            trace_id=_trace_id_for(request),
            field_errors=exc.field_errors,
            retry_after_seconds=exc.retry_after_seconds,
        ),
        headers=_retry_after_headers(exc.retry_after_seconds),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    field_errors = [
        {"field": _strip_loc_prefix(tuple(error["loc"])), "message": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_error_body(
            "VALIDATION_ERROR",
            "Request validation failed.",
            trace_id=_trace_id_for(request),
            field_errors=field_errors,
        ),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_EXCEPTION_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else code
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, message, trace_id=_trace_id_for(request)),
        headers=exc.headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = _trace_id_for(request)
    # Bind trace_id explicitly rather than relying on app.logging's
    # trace_id_var-reading processor: TraceIdMiddleware's `finally` block has
    # already reset that context variable by the time this handler runs (see
    # `_trace_id_for`'s docstring), so the processor would see it empty.
    logger.exception("unhandled_exception", trace_id=trace_id, exc_info=exc)
    settings = getattr(request.app.state, "settings", None)
    https_only = getattr(settings, "environment", None) == "production"
    headers = security_headers_for(request.url.path, https_only=https_only)
    if trace_id:
        headers["X-Request-Id"] = trace_id
    return JSONResponse(
        status_code=500,
        content=_error_body("INTERNAL_ERROR", "An unexpected error occurred.", trace_id=trace_id),
        # ServerErrorMiddleware (Starlette) sends this response via the raw
        # `send` it was given, bypassing TraceIdMiddleware's and
        # SecurityHeadersMiddleware's header injection, so add them here.
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
