"""Security headers, pagination and CSRF helper behaviour (no database)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.pagination import Page, PageParams, page_params
from app.auth.csrf import request_origin
from app.auth.routes import safe_next_path
from app.main import create_app
from app.settings import Settings


def _app() -> FastAPI:
    app = create_app(Settings(_env_file=None, environment="test"))

    @app.get("/api/v1/_unit/items")
    async def _items(params: PageParams = Depends(page_params)) -> Page[int]:
        items = list(range(params.offset, params.offset + params.limit))
        return Page[int](items=items, total=500, limit=params.limit, offset=params.offset)

    @app.get("/other")
    async def _other() -> dict[str, str]:
        return {"ok": "yes"}

    return app


async def _get(path: str, **kwargs: Any) -> Any:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, **kwargs)


async def test_security_headers_on_api_responses() -> None:
    response = await _get("/api/health/live")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


async def test_security_headers_on_errors_and_non_api_paths() -> None:
    missing = await _get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.headers["x-frame-options"] == "DENY"
    assert missing.headers["cache-control"] == "no-store"

    other = await _get("/other")
    assert other.headers["x-content-type-options"] == "nosniff"
    assert "cache-control" not in other.headers


async def test_me_requires_authentication() -> None:
    response = await _get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_pagination_defaults_and_bounds() -> None:
    default = (await _get("/api/v1/_unit/items")).json()
    assert default["limit"] == 50 and default["offset"] == 0 and default["total"] == 500
    assert len(default["items"]) == 50

    ok = (await _get("/api/v1/_unit/items", params={"limit": 200, "offset": 5})).json()
    assert ok["limit"] == 200 and ok["offset"] == 5

    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        bad = await _get("/api/v1/_unit/items", params=params)
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"origin": "http://localhost:5173"}, "http://localhost:5173"),
        ({"referer": "http://localhost:5173/orders?x=1"}, "http://localhost:5173"),
        ({"origin": "null"}, None),
        ({"referer": "not a url"}, None),
        ({}, None),
    ],
)
def test_request_origin(headers: dict[str, str], expected: str | None) -> None:
    raw = [(k.encode(), v.encode()) for k, v in headers.items()]
    assert request_origin(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/", "/"),
        ("/orders/1?tab=a", "/orders/1?tab=a"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("https://evil.example", "/"),
        ("orders", "/"),
        ("/ok\r\nSet-Cookie: x", "/"),
    ],
)
def test_safe_next_path(value: str | None, expected: str) -> None:
    assert safe_next_path(value) == expected


def _prod(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "environment": "production",
        "session_secret": "a-very-long-and-safe-session-secret-0123456789",
        "service_token": "a-very-long-and-safe-service-token-0123456789",
        "public_origin": "https://app.linesense.example.com",
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-live-example-key",
        "oidc_issuer": "https://login.example.com/realms/linesense",
        "oidc_client_secret": "a-real-client-secret-from-the-idp-0123",
        "oidc_redirect_uri": "https://app.linesense.example.com/auth/callback",
    }
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_production_settings_accept_real_identity_provider() -> None:
    assert _prod().environment == "production"


@pytest.mark.parametrize(
    "overrides",
    [
        {"oidc_issuer": "http://127.0.0.1:8090"},
        {"oidc_client_secret": "dev-oidc-client-secret"},
        {"oidc_redirect_uri": "http://localhost:5173/auth/callback"},
    ],
)
def test_production_rejects_development_identity_settings(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="oidc"):
        _prod(**overrides)
