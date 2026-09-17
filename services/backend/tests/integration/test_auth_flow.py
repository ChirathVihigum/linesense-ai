"""End-to-end OIDC login against the in-process development identity provider.

The dev IdP runs under a real uvicorn server on a free port (Authlib fetches
discovery, JWKS and tokens from it over HTTP); the API app is driven through
``httpx.ASGITransport`` at ``http://testserver``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from joserfc.jwk import RSAKey
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent, SessionRecord, User
from app.main import create_app
from app.settings import Settings
from devtools.dev_oidc.app import (
    DevIdpConfig,
    DevIdpHooks,
    IdTokenTamper,
    create_dev_idp_app,
    load_users,
)
from tests.conftest import BACKEND_DIR
from tests.helpers.auth import seed_identity

pytestmark = pytest.mark.integration

API_ORIGIN = "http://testserver"
REDIRECT_URI = f"{API_ORIGIN}/auth/callback"
CLIENT_ID = "linesense-web"
CLIENT_SECRET = "test-oidc-client-secret"
PASSWORD = "test-idp-password"
BANNER = "Development identity provider — not for production"


@dataclass(frozen=True)
class DevIdp:
    issuer: str
    hooks: DevIdpHooks


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def dev_idp() -> Iterator[DevIdp]:
    port = _free_port()
    issuer = f"http://127.0.0.1:{port}"
    hooks = DevIdpHooks()
    idp_app = create_dev_idp_app(
        DevIdpConfig(
            issuer=issuer,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            password=PASSWORD,
            users=load_users(),
        ),
        hooks=hooks,
    )
    server = uvicorn.Server(
        uvicorn.Config(idp_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, name="dev-idp", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("dev IdP did not start")
        time.sleep(0.02)
    try:
        yield DevIdp(issuer=issuer, hooks=hooks)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def auth_settings(settings: Settings, dev_idp: DevIdp) -> Settings:
    return settings.model_copy(
        update={
            "oidc_issuer": dev_idp.issuer,
            "oidc_client_id": CLIENT_ID,
            "oidc_client_secret": SecretStr(CLIENT_SECRET),
            "oidc_redirect_uri": REDIRECT_URI,
            "public_origin": API_ORIGIN,
        }
    )


@pytest.fixture
def auth_app(auth_settings: Settings) -> FastAPI:
    return create_app(auth_settings)


@pytest_asyncio.fixture
async def api(auth_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=auth_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url=API_ORIGIN) as client:
        yield client


@pytest_asyncio.fixture
async def idp_http() -> AsyncGenerator[AsyncClient, None]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


@pytest_asyncio.fixture
async def seeded(session_factory: async_sessionmaker[AsyncSession], dev_idp: DevIdp) -> None:
    async with session_factory() as session:
        await seed_identity(session, issuer=dev_idp.issuer)
        await session.commit()


def _set_cookie_headers(response: httpx.Response, name: str) -> list[str]:
    return [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")]


async def _begin_login(api: AsyncClient, dev_idp: DevIdp, next_path: str | None = None) -> str:
    params = {"next": next_path} if next_path is not None else None
    response = await api.get("/auth/login", params=params)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{dev_idp.issuer}/authorize?")
    query = dict(parse_qsl(urlsplit(location).query))
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"]
    assert query["state"]
    assert query["nonce"]
    assert query["redirect_uri"] == REDIRECT_URI
    return location


async def _submit_credentials(
    idp_http: AsyncClient, authorize_url: str, *, subject: str, password: str = PASSWORD
) -> httpx.Response:
    page = await idp_http.get(authorize_url)
    assert page.status_code == 200
    assert BANNER in page.text
    params = dict(parse_qsl(urlsplit(authorize_url).query))
    assert f'value="{params["state"]}"' in page.text
    return await idp_http.post(
        authorize_url.split("?", 1)[0],
        data={**params, "sub": subject, "password": password},
    )


async def _obtain_callback_url(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    *,
    subject: str = "dev|planner",
    next_path: str | None = None,
) -> str:
    authorize_url = await _begin_login(api, dev_idp, next_path)
    response = await _submit_credentials(idp_http, authorize_url, subject=subject)
    assert response.status_code == 302, response.text
    callback_url = response.headers["location"]
    assert callback_url.startswith(f"{REDIRECT_URI}?")
    return callback_url


async def _login(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    *,
    subject: str = "dev|planner",
    next_path: str | None = None,
) -> httpx.Response:
    callback_url = await _obtain_callback_url(
        api, idp_http, dev_idp, subject=subject, next_path=next_path
    )
    return await api.get(callback_url)


async def test_full_login_flow(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await _login(api, idp_http, dev_idp, next_path="/orders/123?tab=materials")

    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/orders/123?tab=materials"
    session_cookies = _set_cookie_headers(response, "ls_session")
    assert len(session_cookies) == 1
    cookie = session_cookies[0].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" not in cookie
    assert "path=/" in cookie
    # No provider token ever reaches the browser.
    assert "id_token" not in response.text
    assert "access_token" not in response.text

    me = await api.get("/api/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "planner@demo.test"
    assert body["user"]["display_name"] == "Planner (KTN)"
    assert body["organization"]["name"] == "Demo Apparel Group"
    assert [f["code"] for f in body["factories"]] == ["KTN"]
    ktn = body["factories"][0]
    assert ktn["roles"] == ["planner"]
    assert "order:create" in ktn["permissions"]
    assert "order:dispatch" not in ktn["permissions"]
    assert ktn["timezone"] == "Asia/Colombo"
    assert isinstance(body["csrf_token"], str) and len(body["csrf_token"]) >= 32

    assert me.headers["cache-control"] == "no-store"
    assert me.headers["x-content-type-options"] == "nosniff"

    async with session_factory() as session:
        events = (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login"))
        ).all()
        stored = (await session.scalars(select(SessionRecord))).all()
    assert [(e.outcome, e.actor_type, e.target_type) for e in events] == [
        ("SUCCESS", "USER", "user")
    ]
    assert len(stored) == 1
    # Only the hash of the raw cookie token is persisted.
    raw = api.cookies.get("ls_session")
    assert raw is not None
    assert stored[0].token_hash != raw
    assert len(stored[0].token_hash) == 64


async def test_callback_updates_existing_user_profile(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(User)
            .where(User.subject == "dev|planner")
            .values(email="old@demo.test", display_name="Old Name")
        )
        await session.commit()

    response = await _login(api, idp_http, dev_idp)
    assert response.status_code == 302

    async with session_factory() as session:
        users = (await session.scalars(select(User).where(User.subject == "dev|planner"))).all()
    assert len(users) == 1
    assert users[0].email == "planner@demo.test"
    assert users[0].display_name == "Planner (KTN)"


def _wrong_audience(claims: dict[str, Any], key: RSAKey) -> tuple[dict[str, Any], RSAKey]:
    # azp names our client, which Authlib's azp check alone would accept.
    return {**claims, "aud": "some-other-client", "azp": CLIENT_ID}, key


def _wrong_issuer(claims: dict[str, Any], key: RSAKey) -> tuple[dict[str, Any], RSAKey]:
    return {**claims, "iss": "http://evil.example"}, key


def _expired(claims: dict[str, Any], key: RSAKey) -> tuple[dict[str, Any], RSAKey]:
    now = int(time.time())
    return {**claims, "iat": now - 3600, "exp": now - 1800}, key


def _nonce_mismatch(claims: dict[str, Any], key: RSAKey) -> tuple[dict[str, Any], RSAKey]:
    return {**claims, "nonce": "not-the-nonce-we-sent"}, key


def _missing_audience(claims: dict[str, Any], key: RSAKey) -> tuple[dict[str, Any], RSAKey]:
    return {k: v for k, v in claims.items() if k != "aud"}, key


def _bad_signature(claims: dict[str, Any], key: RSAKey) -> tuple[dict[str, Any], RSAKey]:
    # Same kid as the published key, different key material.
    forged = RSAKey.generate_key(2048, parameters={"kid": key.kid})
    return claims, forged


@pytest.mark.parametrize(
    "tamper",
    [
        _wrong_audience,
        _wrong_issuer,
        _expired,
        _nonce_mismatch,
        _missing_audience,
        _bad_signature,
    ],
)
async def test_invalid_id_token_is_rejected(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
    tamper: IdTokenTamper,
) -> None:
    dev_idp.hooks.id_token_tamper = tamper
    try:
        response = await _login(api, idp_http, dev_idp)
    finally:
        dev_idp.hooks.id_token_tamper = None

    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/login?error=auth_failed"
    assert _set_cookie_headers(response, "ls_session") == []
    assert (await api.get("/api/v1/me")).status_code == 401
    async with session_factory() as session:
        assert (await session.scalars(select(SessionRecord))).all() == []

    # The same client logs in fine once the provider is honest again.
    assert (await _login(api, idp_http, dev_idp)).headers["location"] == f"{API_ORIGIN}/"


async def test_discovery_without_jwks_uri_fails_cleanly(
    api: AsyncClient,
    auth_app: FastAPI,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
) -> None:
    callback_url = await _obtain_callback_url(api, idp_http, dev_idp)
    # Authlib raises RuntimeError when the (cached) metadata has no jwks_uri.
    metadata = auth_app.state.oauth.create_client("linesense").server_metadata
    metadata.pop("jwks_uri")
    metadata.pop("jwks", None)
    response = await api.get(callback_url)
    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/login?error=auth_failed"
    assert _set_cookie_headers(response, "ls_session") == []


async def test_configured_issuer_with_trailing_slash_is_normalized(
    auth_settings: Settings,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(auth_settings.model_copy(update={"oidc_issuer": dev_idp.issuer + "/"}))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url=API_ORIGIN
    ) as client:
        response = await _login(client, idp_http, dev_idp)
        assert response.headers["location"] == f"{API_ORIGIN}/"
        assert (await client.get("/api/v1/me")).status_code == 200

    async with session_factory() as session:
        issuers = set((await session.scalars(select(User.issuer))).all())
    assert issuers == {dev_idp.issuer}


async def test_issuer_mismatch_with_discovery_is_refused(
    auth_settings: Settings, dev_idp: DevIdp
) -> None:
    # Same server, but the discovery document names a different issuer string.
    alias = dev_idp.issuer.replace("127.0.0.1", "localhost")
    app = create_app(auth_settings.model_copy(update={"oidc_issuer": alias}))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url=API_ORIGIN
    ) as client:
        response = await client.get("/auth/login")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


async def test_wrong_password_returns_401_page_without_code(
    api: AsyncClient, idp_http: AsyncClient, dev_idp: DevIdp
) -> None:
    authorize_url = await _begin_login(api, dev_idp)
    response = await _submit_credentials(
        idp_http, authorize_url, subject="dev|planner", password="wrong-password"
    )
    assert response.status_code == 401
    assert "location" not in response.headers
    assert "code=" not in response.text
    assert BANNER in response.text


async def test_tampered_state_fails_without_session(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    callback_url = await _obtain_callback_url(api, idp_http, dev_idp)
    parts = urlsplit(callback_url)
    query = dict(parse_qsl(parts.query))
    query["state"] = query["state"] + "tampered"
    response = await api.get("/auth/callback", params=query)

    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/login?error=auth_failed"
    assert _set_cookie_headers(response, "ls_session") == []
    assert (await api.get("/api/v1/me")).status_code == 401
    async with session_factory() as session:
        assert (await session.scalars(select(SessionRecord))).all() == []


async def test_replayed_code_is_rejected(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
) -> None:
    first_callback = await _obtain_callback_url(api, idp_http, dev_idp)
    first_code = dict(parse_qsl(urlsplit(first_callback).query))["code"]
    assert (await api.get(first_callback)).status_code == 302
    api.cookies.clear()

    # A fresh, valid login handshake (new state + PKCE verifier) presented with
    # the already-redeemed code must fail at the IdP token endpoint.
    second_callback = await _obtain_callback_url(api, idp_http, dev_idp)
    second_state = dict(parse_qsl(urlsplit(second_callback).query))["state"]
    response = await api.get("/auth/callback", params={"code": first_code, "state": second_state})
    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/login?error=auth_failed"
    assert _set_cookie_headers(response, "ls_session") == []

    # Direct replay at the token endpoint is also refused.
    token_response = await idp_http.post(
        f"{dev_idp.issuer}/token",
        data={
            "grant_type": "authorization_code",
            "code": first_code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": "x" * 43,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    assert token_response.status_code == 400
    assert token_response.json() == {"error": "invalid_grant"}


async def test_identity_without_membership_is_forbidden(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # No seed: the callback creates the user row but never a membership.
    response = await _login(api, idp_http, dev_idp, subject="dev|viewer")
    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/"

    me = await api.get("/api/v1/me")
    assert me.status_code == 403
    assert me.json()["error"]["code"] == "FORBIDDEN"
    assert me.json()["error"]["message"] == "No LineSense membership"

    async with session_factory() as session:
        users = (await session.scalars(select(User))).all()
        events = (await session.scalars(select(AuditEvent))).all()
    assert [u.email for u in users] == ["viewer@demo.test"]
    assert events == []  # no organization to attribute the event to


@pytest.mark.parametrize(
    "next_path",
    ["//evil.example", "https://evil.example/x", "/\\evil.example", "orders", ""],
)
async def test_unsafe_next_is_ignored(
    api: AsyncClient, idp_http: AsyncClient, dev_idp: DevIdp, seeded: None, next_path: str
) -> None:
    response = await _login(api, idp_http, dev_idp, next_path=next_path)
    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/"


async def test_logout_revokes_session(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _login(api, idp_http, dev_idp)
    csrf_token = (await api.get("/api/v1/me")).json()["csrf_token"]
    stolen_cookie = api.cookies.get("ls_session")

    response = await api.post(
        "/auth/logout", headers={"X-CSRF-Token": csrf_token, "Origin": API_ORIGIN}
    )
    assert response.status_code == 204
    cleared = _set_cookie_headers(response, "ls_session")
    assert len(cleared) == 1
    assert "max-age=0" in cleared[0].lower()

    assert (await api.get("/api/v1/me")).status_code == 401
    # The old raw token no longer works even if replayed.
    assert stolen_cookie is not None
    api.cookies.set("ls_session", stolen_cookie)
    assert (await api.get("/api/v1/me")).status_code == 401

    async with session_factory() as session:
        record = await session.scalar(select(SessionRecord))
        actions = (await session.scalars(select(AuditEvent.action))).all()
    assert record is not None and record.revoked_at is not None
    assert "auth.logout" in actions


async def test_login_rotates_existing_session(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _login(api, idp_http, dev_idp)
    first_cookie = api.cookies.get("ls_session")
    await _login(api, idp_http, dev_idp)
    second_cookie = api.cookies.get("ls_session")
    assert first_cookie and second_cookie and first_cookie != second_cookie

    async with session_factory() as session:
        records = (await session.scalars(select(SessionRecord))).all()
    assert len(records) == 2
    assert sum(1 for r in records if r.revoked_at is None) == 1


async def test_inactive_user_login_fails_and_is_audited(
    api: AsyncClient,
    idp_http: AsyncClient,
    dev_idp: DevIdp,
    seeded: None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.subject == "dev|planner").values(is_active=False)
        )
        await session.commit()

    response = await _login(api, idp_http, dev_idp)
    assert response.status_code == 302
    assert response.headers["location"] == f"{API_ORIGIN}/login?error=auth_failed"
    assert _set_cookie_headers(response, "ls_session") == []

    async with session_factory() as session:
        events = (await session.scalars(select(AuditEvent))).all()
        assert (await session.scalars(select(SessionRecord))).all() == []
    assert [(e.action, e.outcome) for e in events] == [("auth.login", "FAILED")]


async def test_org_wide_admin_sees_all_factories(
    api: AsyncClient, idp_http: AsyncClient, dev_idp: DevIdp, seeded: None
) -> None:
    await _login(api, idp_http, dev_idp, subject="dev|admin")
    body = (await api.get("/api/v1/me")).json()
    assert sorted(f["code"] for f in body["factories"]) == ["BYG", "KTN"]
    for factory in body["factories"]:
        assert factory["roles"] == ["org_admin"]
        assert "admin:manage" in factory["permissions"]
        assert "quality:release" not in factory["permissions"]


async def test_login_page_lists_demo_users(idp_http: AsyncClient, dev_idp: DevIdp) -> None:
    discovery = (await idp_http.get(f"{dev_idp.issuer}/.well-known/openid-configuration")).json()
    assert discovery["issuer"] == dev_idp.issuer
    assert discovery["code_challenge_methods_supported"] == ["S256"]
    assert len(load_users()) == 10


def test_dev_idp_refuses_to_start_in_production() -> None:
    env = {**os.environ, "LS_ENVIRONMENT": "production"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "devtools.dev_oidc"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "development" in result.stderr and "test" in result.stderr
