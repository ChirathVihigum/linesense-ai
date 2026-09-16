from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.errors import AppError
from app.main import create_app
from app.settings import Settings


class _Payload(BaseModel):
    name: str


def _build_test_app() -> FastAPI:
    settings = Settings(_env_file=None, environment="test")
    application = create_app(settings)

    @application.post("/__test/conflict")
    async def _conflict() -> None:
        raise AppError(409, "CONFLICT", "x")

    @application.post("/__test/boom")
    async def _boom() -> None:
        raise RuntimeError("secret detail")

    @application.post("/__test/validate")
    async def _validate(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    return application


@pytest.fixture
async def error_client() -> AsyncIterator[AsyncClient]:
    application = _build_test_app()
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_app_error_returns_contract_body(error_client: AsyncClient) -> None:
    response = await error_client.post("/__test/conflict")

    assert response.status_code == 409
    trace_id = response.headers["X-Request-Id"]
    assert response.json() == {
        "error": {
            "code": "CONFLICT",
            "message": "x",
            "field_errors": [],
            "trace_id": trace_id,
            "retry_after_seconds": None,
        }
    }


async def test_unhandled_exception_returns_500_without_leaking_detail(
    error_client: AsyncClient,
) -> None:
    response = await error_client.post("/__test/boom")

    assert response.status_code == 500
    assert "secret detail" not in response.text
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["trace_id"] == response.headers["X-Request-Id"]


async def test_validation_error_returns_field_errors(error_client: AsyncClient) -> None:
    response = await error_client.post("/__test/validate", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["field_errors"] == [{"field": "name", "message": "Field required"}]


async def test_invalid_inbound_trace_id_is_replaced(error_client: AsyncClient) -> None:
    response = await error_client.post("/__test/conflict", headers={"X-Request-Id": "not-a-uuid"})

    assert response.headers["X-Request-Id"] != "not-a-uuid"


async def test_valid_inbound_trace_id_is_echoed(error_client: AsyncClient) -> None:
    request_id = str(uuid.uuid4())

    response = await error_client.post("/__test/conflict", headers={"X-Request-Id": request_id})

    assert response.headers["X-Request-Id"] == request_id
