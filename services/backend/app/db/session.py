"""Async SQLAlchemy engine/session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import cache

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings, normalize_database_url


def is_supabase_url(url: str) -> bool:
    """Return True when ``url`` points at a hosted Supabase Postgres instance."""
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname.endswith(".supabase.co") or hostname.endswith(".supabase.com")


def _ensure_supabase_sslmode(url: str) -> str:
    """Ensure ``sslmode=require`` is present for hosted Supabase connections."""
    if not is_supabase_url(url):
        return url
    parts = urlsplit(url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "sslmode" not in query_params:
        query_params["sslmode"] = "require"
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query_params), parts.fragment)
        )
    return url


def engine_connect_args(url: str) -> dict[str, Any]:
    """Return driver ``connect_args`` appropriate for ``url``.

    Supabase's Supavisor / PgBouncer transaction pooler (port 6543 or
    ``*.pooler.supabase.com``) multiplexes backend connections across
    transactions, which breaks psycopg3's server-side prepared statement cache
    unless ``prepare_threshold=None`` is set.
    """
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    if (
        is_supabase_url(url)
        or "pooler.supabase." in hostname
        or parts.port == 6543
    ):
        return {"prepare_threshold": None}
    return {}


@cache
def get_engine(url: str) -> AsyncEngine:
    """Return a cached async engine for ``url``, creating it on first use.

    ``hide_parameters`` keeps bound values (which may be user data) out of
    exception messages and logs.
    """
    normalized = _ensure_supabase_sslmode(normalize_database_url(url))
    connect_args = engine_connect_args(normalized)
    return create_async_engine(
        normalized,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args=connect_args,
    )


@cache
def get_session_factory(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    """Return a cached session factory bound to the engine for ``url``.

    Defaults to ``get_settings().database_url`` when ``url`` is omitted.
    """
    resolved_url = normalize_database_url(url or get_settings().database_url)
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
