"""Async SQLAlchemy engine/session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import cache

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings


@cache
def get_engine(url: str) -> AsyncEngine:
    """Return a cached async engine for ``url``, creating it on first use.

    ``hide_parameters`` keeps bound values (which may be user data) out of
    exception messages and logs.
    """
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True)


@cache
def get_session_factory(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    """Return a cached session factory bound to the engine for ``url``.

    Defaults to ``get_settings().database_url`` when ``url`` is omitted.
    """
    resolved_url = url or get_settings().database_url
    engine = get_engine(resolved_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an ``AsyncSession`` bound to the current app.

    Commits on success, rolls back and re-raises on exception.
    """
    settings = request.app.state.settings
    session_factory = get_session_factory(settings.database_url)
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
