"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.middleware import TraceIdMiddleware
from app.logging import configure_logging
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)

    app = FastAPI(title="LineSense AI", version="0.1.0")
    app.state.settings = resolved_settings

    app.add_middleware(TraceIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)

    return app
