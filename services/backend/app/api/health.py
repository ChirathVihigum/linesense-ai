"""Liveness and readiness health checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.session import get_db_session

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def health_ready(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
        result = await session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        pgvector_version = result.scalar_one_or_none()
    except Exception as exc:
        raise AppError(503, "SERVICE_UNAVAILABLE", "Database is not ready.") from exc

    if pgvector_version is None:
        raise AppError(503, "SERVICE_UNAVAILABLE", "pgvector extension is not installed.")

    return {"status": "ok", "database": "ok", "pgvector": pgvector_version}
