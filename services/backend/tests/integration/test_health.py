from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_health_live_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_returns_ok_with_pgvector_version(client: AsyncClient) -> None:
    response = await client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "pgvector": "0.8.6"}
