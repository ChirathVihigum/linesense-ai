from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_principal
from app.auth.policy import Principal
from app.main import create_app
from app.settings import Settings
from tests.helpers.auth import login_as

pytestmark = pytest.mark.integration

_test_router = APIRouter(prefix="/api/v1/_test")


@_test_router.post("/echo")
async def _echo(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {"user_id": str(principal.user_id)}


@_test_router.get("/echo")
async def _echo_get(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {"user_id": str(principal.user_id)}


@_test_router.post("/open")
async def _open() -> dict[str, str]:
    return {"status": "ok"}


@pytest.fixture
def csrf_app(settings: Settings) -> FastAPI:
    app = create_app(settings)
    app.include_router(_test_router)
    return app


@pytest_asyncio.fixture
async def csrf_client(csrf_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=csrf_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_post_without_token_is_rejected(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await csrf_client.post("/api/v1/_test/echo", headers={"Origin": authed.origin})
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "CSRF_FAILED"
    assert body["error"]["trace_id"]


async def test_wrong_token_is_rejected(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await authed.post("/api/v1/_test/echo", headers={"X-CSRF-Token": "not-it"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_wrong_origin_is_rejected(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await authed.post("/api/v1/_test/echo", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_missing_origin_and_referer_is_rejected(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await csrf_client.post(
        "/api/v1/_test/echo", headers={"X-CSRF-Token": authed.csrf_token}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_referer_origin_is_accepted_as_fallback(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await csrf_client.post(
        "/api/v1/_test/echo",
        headers={"X-CSRF-Token": authed.csrf_token, "Referer": f"{authed.origin}/orders/1"},
    )
    assert response.status_code == 200


async def test_correct_token_and_origin_passes(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await authed.post("/api/v1/_test/echo")
    assert response.status_code == 200
    assert response.json() == {"user_id": str(authed.user.id)}


async def test_get_does_not_require_token(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await csrf_client.get("/api/v1/_test/echo")
    assert response.status_code == 200


async def test_unauthenticated_post_with_origin_reaches_auth_check(
    csrf_client: AsyncClient, settings: Settings
) -> None:
    response = await csrf_client.post(
        "/api/v1/_test/echo", headers={"Origin": settings.public_origin}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_unauthenticated_post_still_requires_origin(csrf_client: AsyncClient) -> None:
    response = await csrf_client.post("/api/v1/_test/open")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"


async def test_logout_is_csrf_protected(
    csrf_client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    authed = await login_as(csrf_client, session_factory, "planner@demo.test")
    response = await csrf_client.post("/auth/logout", headers={"Origin": authed.origin})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert (await csrf_client.get("/api/v1/me")).status_code == 200


async def test_internal_paths_are_exempt(csrf_client: AsyncClient) -> None:
    # No /internal route exists yet: reaching the router (404) proves the
    # CSRF middleware did not intercept the request.
    response = await csrf_client.post("/internal/v1/agent-tasks")
    assert response.status_code == 404
