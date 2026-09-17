"""Unit tests for the development OIDC provider (no database, no network)."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from joserfc import jwt
from joserfc.jwk import KeySet

from devtools.dev_oidc.app import USERS_FILE, DevIdpConfig, create_dev_idp_app, load_users
from tests.helpers.auth import DEMO_IDENTITIES

ISSUER = "http://idp.test"
CLIENT_ID = "client-1"
CLIENT_SECRET = "secret-1"
REDIRECT_URI = "http://app.test/auth/callback"
PASSWORD = "pw-1"
VERIFIER = "v" * 50


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


AUTH_PARAMS = {
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "response_type": "code",
    "scope": "openid email profile",
    "state": "state-<1>",
    "nonce": "nonce-1",
    "code_challenge": _challenge(VERIFIER),
    "code_challenge_method": "S256",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest_asyncio.fixture
async def idp(clock: Clock) -> AsyncGenerator[AsyncClient, None]:
    app = create_dev_idp_app(
        DevIdpConfig(
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            password=PASSWORD,
            users=load_users(),
        ),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ISSUER) as client:
        yield client


async def _code(idp: AsyncClient, sub: str = "dev|planner") -> str:
    response = await idp.post("/authorize", data={**AUTH_PARAMS, "sub": sub, "password": PASSWORD})
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    query = dict(parse_qsl(location.query))
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
    assert query["state"] == AUTH_PARAMS["state"]
    return query["code"]


def _token_form(code: str, **overrides: str) -> dict[str, str]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": VERIFIER,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    form.update(overrides)
    return form


def test_users_json_matches_demo_identities() -> None:
    users = json.loads(Path(USERS_FILE).read_text(encoding="utf-8"))
    assert [(u["sub"], u["email"], u["name"]) for u in users] == [
        (subject, email, name) for email, subject, name, _role, _factory in DEMO_IDENTITIES
    ]
    for email, subject, *_ in DEMO_IDENTITIES:
        assert subject == f"dev|{email.split('@', 1)[0]}"


async def test_discovery_document(idp: AsyncClient) -> None:
    doc = (await idp.get("/.well-known/openid-configuration")).json()
    assert doc["issuer"] == ISSUER
    assert doc["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert doc["token_endpoint"] == f"{ISSUER}/token"
    assert doc["jwks_uri"] == f"{ISSUER}/jwks"
    assert doc["userinfo_endpoint"] == f"{ISSUER}/userinfo"
    assert doc["response_types_supported"] == ["code"]
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["id_token_signing_alg_values_supported"] == ["RS256"]
    assert doc["token_endpoint_auth_methods_supported"] == [
        "client_secret_post",
        "client_secret_basic",
    ]


async def test_jwks_exposes_public_key_only(idp: AsyncClient) -> None:
    keys = (await idp.get("/jwks")).json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA"
    assert keys[0]["kid"]
    assert "d" not in keys[0]


async def test_authorize_page_escapes_and_lists_users(idp: AsyncClient) -> None:
    page = await idp.get("/authorize", params=AUTH_PARAMS)
    assert page.status_code == 200
    assert "Development identity provider — not for production" in page.text
    assert 'value="state-&lt;1&gt;"' in page.text
    assert "state-<1>" not in page.text
    assert page.text.count("<option ") == 10
    assert "Planner (KTN)" in page.text
    assert 'type="password"' in page.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_id", "other"),
        ("redirect_uri", REDIRECT_URI + "/extra"),
        ("response_type", "token"),
        ("code_challenge_method", "plain"),
        ("state", ""),
        ("nonce", ""),
        ("code_challenge", ""),
    ],
)
async def test_authorize_rejects_invalid_requests(idp: AsyncClient, field: str, value: str) -> None:
    params = {**AUTH_PARAMS, field: value}
    response = await idp.get("/authorize", params=params)
    assert response.status_code == 400
    assert "location" not in response.headers
    post = await idp.post("/authorize", data={**params, "sub": "dev|planner", "password": PASSWORD})
    assert post.status_code == 400
    assert "location" not in post.headers


async def test_authorize_rejects_unknown_user(idp: AsyncClient) -> None:
    response = await idp.post(
        "/authorize", data={**AUTH_PARAMS, "sub": "dev|nobody", "password": PASSWORD}
    )
    assert response.status_code == 400


async def test_wrong_password_is_401(idp: AsyncClient) -> None:
    response = await idp.post(
        "/authorize", data={**AUTH_PARAMS, "sub": "dev|planner", "password": "nope"}
    )
    assert response.status_code == 401
    assert "location" not in response.headers


async def test_token_exchange_issues_valid_id_token(idp: AsyncClient, clock: Clock) -> None:
    code = await _code(idp)
    response = await idp.post("/token", data=_token_form(code))
    assert response.status_code == 200
    token = response.json()
    assert token["token_type"] == "Bearer"
    assert token["expires_in"] == 300
    assert token["access_token"]

    keys = KeySet.import_key_set((await idp.get("/jwks")).json())
    decoded = jwt.decode(token["id_token"], keys, algorithms=["RS256"])
    assert decoded.header["alg"] == "RS256"
    claims = decoded.claims
    assert claims["iss"] == ISSUER
    assert claims["sub"] == "dev|planner"
    assert claims["aud"] == CLIENT_ID
    assert claims["iat"] == int(clock.now)
    assert claims["exp"] == int(clock.now) + 300
    assert claims["nonce"] == "nonce-1"
    assert claims["email"] == "planner@demo.test"
    assert claims["name"] == "Planner (KTN)"

    userinfo = await idp.get(
        "/userinfo", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert userinfo.status_code == 200
    assert userinfo.json() == {
        "sub": "dev|planner",
        "email": "planner@demo.test",
        "name": "Planner (KTN)",
    }


async def test_token_accepts_basic_client_auth(idp: AsyncClient) -> None:
    code = await _code(idp)
    form = _token_form(code)
    del form["client_secret"]
    del form["client_id"]
    response = await idp.post("/token", data=form, auth=(CLIENT_ID, CLIENT_SECRET))
    assert response.status_code == 200


async def test_token_rejects_bad_client(idp: AsyncClient) -> None:
    code = await _code(idp)
    response = await idp.post("/token", data=_token_form(code, client_secret="wrong"))
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_verifier": "w" * 50},
        {"redirect_uri": "http://app.test/other"},
        {"code": "unknown-code"},
    ],
)
async def test_token_rejects_invalid_grants(idp: AsyncClient, overrides: dict[str, str]) -> None:
    code = await _code(idp)
    form = {**_token_form(code), **overrides}
    response = await idp.post("/token", data=form)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_grant"}


async def test_failed_exchange_consumes_the_code(idp: AsyncClient) -> None:
    code = await _code(idp)
    bad = await idp.post("/token", data=_token_form(code, code_verifier="w" * 50))
    assert bad.status_code == 400
    retry = await idp.post("/token", data=_token_form(code))
    assert retry.status_code == 400
    assert retry.json() == {"error": "invalid_grant"}


async def test_code_is_single_use(idp: AsyncClient) -> None:
    code = await _code(idp)
    assert (await idp.post("/token", data=_token_form(code))).status_code == 200
    replay = await idp.post("/token", data=_token_form(code))
    assert replay.status_code == 400
    assert replay.json() == {"error": "invalid_grant"}


async def test_expired_code_is_rejected(idp: AsyncClient, clock: Clock) -> None:
    code = await _code(idp)
    clock.now += 61
    response = await idp.post("/token", data=_token_form(code))
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_grant"}


async def test_unsupported_grant_type(idp: AsyncClient) -> None:
    code = await _code(idp)
    response = await idp.post("/token", data=_token_form(code, grant_type="password"))
    assert response.status_code == 400
    assert response.json() == {"error": "unsupported_grant_type"}


async def test_userinfo_rejects_bad_or_expired_tokens(idp: AsyncClient, clock: Clock) -> None:
    assert (await idp.get("/userinfo")).status_code == 401
    bad = await idp.get("/userinfo", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    code = await _code(idp)
    access_token = (await idp.post("/token", data=_token_form(code))).json()["access_token"]
    clock.now += 301
    expired = await idp.get("/userinfo", headers={"Authorization": f"Bearer {access_token}"})
    assert expired.status_code == 401
