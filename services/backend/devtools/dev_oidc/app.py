"""A minimal OpenID Connect provider for development and tests only.

Implements just enough of OIDC (discovery, JWKS, authorization-code flow with
mandatory PKCE S256, token endpoint, userinfo) for the LineSense Authlib
client to perform a real login round trip against the seeded demo
identities. It is **not** a production identity provider: users are a static
file, every user shares one password, state lives in process memory and the
signing key is regenerated at every start. See ADR-0004.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from joserfc import jwt
from joserfc.jwk import RSAKey

USERS_FILE = Path(__file__).resolve().parent / "users.json"
BANNER = "Development identity provider — not for production"
CODE_TTL_SECONDS = 60
TOKEN_TTL_SECONDS = 300

_AUTHORIZE_FIELDS = (
    "client_id",
    "redirect_uri",
    "response_type",
    "scope",
    "state",
    "nonce",
    "code_challenge",
    "code_challenge_method",
)


@dataclass(frozen=True)
class DevUser:
    sub: str
    email: str
    name: str


def load_users(path: Path = USERS_FILE) -> dict[str, DevUser]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {entry["sub"]: DevUser(**entry) for entry in raw}


@dataclass(frozen=True)
class DevIdpConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    password: str
    users: Mapping[str, DevUser]


@dataclass
class _CodeGrant:
    sub: str
    client_id: str
    redirect_uri: str
    nonce: str
    code_challenge: str
    expires_at: float


@dataclass
class _AccessGrant:
    sub: str
    expires_at: float


@dataclass
class _State:
    codes: dict[str, _CodeGrant] = field(default_factory=dict)
    access_tokens: dict[str, _AccessGrant] = field(default_factory=dict)


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 28rem; margin: 2rem auto; padding: 0 1rem; }}
.banner {{ background: #7a1c1c; color: #fff; padding: .75rem 1rem; border-radius: .375rem; }}
label {{ display: block; margin-top: 1rem; }}
select, input {{ width: 100%; padding: .5rem; margin-top: .25rem; }}
button {{ margin-top: 1.25rem; padding: .5rem 1rem; }}
.error {{ color: #7a1c1c; }}
</style></head>
<body>
<p class="banner" role="alert">{escape(BANNER)}</p>
<h1>{escape(title)}</h1>
{body}
</body></html>"""
    return HTMLResponse(
        html,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'",
        },
    )


def _error_page(message: str, status_code: int) -> HTMLResponse:
    return _page("Sign-in error", f'<p class="error">{escape(message)}</p>', status_code)


def create_dev_idp_app(config: DevIdpConfig, *, clock: Callable[[], float] = time.time) -> FastAPI:
    issuer = config.issuer.rstrip("/")
    signing_key = RSAKey.generate_key(2048, auto_kid=True)
    kid = signing_key.kid
    assert kid is not None
    public_jwk = signing_key.as_dict(private=False)
    state = _State()

    app = FastAPI(title="LineSense development OIDC provider", docs_url=None, redoc_url=None)

    def validate_authorize(params: Mapping[str, str]) -> str | None:
        """Return an error message, or ``None`` if the request is acceptable."""
        if not _equal(params.get("client_id", ""), config.client_id):
            return "Unknown client."
        if params.get("redirect_uri", "") != config.redirect_uri:
            return "redirect_uri is not registered for this client."
        if params.get("response_type") != "code":
            return "Only response_type=code is supported."
        if params.get("code_challenge_method") != "S256":
            return "PKCE with code_challenge_method=S256 is required."
        if not params.get("code_challenge"):
            return "code_challenge is required."
        if not params.get("state"):
            return "state is required."
        if not params.get("nonce"):
            return "nonce is required."
        if "openid" not in params.get("scope", "").split():
            return "scope must include openid."
        return None

    def render_login(params: Mapping[str, str], error: str | None, status_code: int) -> Response:
        hidden = "\n".join(
            f'<input type="hidden" name="{name}" value="{escape(params.get(name, ""))}">'
            for name in _AUTHORIZE_FIELDS
        )
        options = "\n".join(
            f'<option value="{escape(user.sub)}">{escape(user.name)} — '
            f"{escape(user.email)}</option>"
            for user in config.users.values()
        )
        error_html = f'<p class="error">{escape(error)}</p>' if error else ""
        body = f"""{error_html}
<form method="post" action="{escape(issuer)}/authorize">
{hidden}
<label for="sub">Demo identity</label>
<select id="sub" name="sub">
{options}
</select>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form>"""
        return _page("Sign in to LineSense (development)", body, status_code)

    @app.get("/.well-known/openid-configuration")
    async def discovery() -> dict[str, Any]:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
            "userinfo_endpoint": f"{issuer}/userinfo",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "grant_types_supported": ["authorization_code"],
            "scopes_supported": ["openid", "email", "profile"],
            "claims_supported": ["iss", "sub", "aud", "exp", "iat", "nonce", "email", "name"],
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post",
                "client_secret_basic",
            ],
        }

    @app.get("/jwks")
    async def jwks() -> dict[str, Any]:
        return {"keys": [{**public_jwk, "use": "sig", "alg": "RS256"}]}

    @app.get("/authorize")
    async def authorize_form(request: Request) -> Response:
        params = dict(request.query_params)
        error = validate_authorize(params)
        if error is not None:
            return _error_page(error, 400)
        return render_login(params, None, 200)

    @app.post("/authorize")
    async def authorize_submit(request: Request) -> Response:
        form = await request.form()
        params = {name: str(form.get(name, "")) for name in _AUTHORIZE_FIELDS}
        error = validate_authorize(params)
        if error is not None:
            return _error_page(error, 400)
        user = config.users.get(str(form.get("sub", "")))
        if user is None:
            return _error_page("Unknown user.", 400)
        if not _equal(str(form.get("password", "")), config.password):
            return render_login(params, "Invalid password.", 401)

        code = secrets.token_urlsafe(32)
        now = clock()
        # Drop expired codes so the in-memory store cannot grow without bound.
        for stale in [c for c, g in state.codes.items() if g.expires_at <= now]:
            del state.codes[stale]
        state.codes[code] = _CodeGrant(
            sub=user.sub,
            client_id=params["client_id"],
            redirect_uri=params["redirect_uri"],
            nonce=params["nonce"],
            code_challenge=params["code_challenge"],
            expires_at=now + CODE_TTL_SECONDS,
        )
        query = urlencode({"code": code, "state": params["state"]})
        return RedirectResponse(f"{params['redirect_uri']}?{query}", status_code=302)

    def client_credentials(request: Request, form: Mapping[str, Any]) -> tuple[str, str]:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return "", ""
            client_id, _, client_secret = decoded.partition(":")
            return client_id, client_secret
        return str(form.get("client_id", "")), str(form.get("client_secret", ""))

    def token_error(error: str, status_code: int = 400) -> JSONResponse:
        return JSONResponse(
            {"error": error}, status_code=status_code, headers={"Cache-Control": "no-store"}
        )

    @app.post("/token")
    async def token(request: Request) -> Response:
        form = await request.form()
        client_id, client_secret = client_credentials(request, form)
        if not (
            _equal(client_id, config.client_id) and _equal(client_secret, config.client_secret)
        ):
            return token_error("invalid_client", 401)
        if form.get("grant_type") != "authorization_code":
            return token_error("unsupported_grant_type")

        # Codes are single use: any redemption attempt consumes the code.
        grant = state.codes.pop(str(form.get("code", "")), None)
        now = clock()
        verifier = str(form.get("code_verifier", ""))
        if (
            grant is None
            or grant.expires_at <= now
            or grant.client_id != client_id
            or grant.redirect_uri != str(form.get("redirect_uri", ""))
            or not verifier
            or not _equal(_s256(verifier), grant.code_challenge)
        ):
            return token_error("invalid_grant")

        user = config.users[grant.sub]
        issued_at = int(now)
        id_token = jwt.encode(
            {"alg": "RS256", "kid": kid},
            {
                "iss": issuer,
                "sub": user.sub,
                "aud": config.client_id,
                "exp": issued_at + TOKEN_TTL_SECONDS,
                "iat": issued_at,
                "nonce": grant.nonce,
                "email": user.email,
                "name": user.name,
            },
            signing_key,
            algorithms=["RS256"],
        )
        access_token = secrets.token_urlsafe(32)
        for stale in [t for t, g in state.access_tokens.items() if g.expires_at <= now]:
            del state.access_tokens[stale]
        state.access_tokens[access_token] = _AccessGrant(
            sub=user.sub, expires_at=now + TOKEN_TTL_SECONDS
        )
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": TOKEN_TTL_SECONDS,
                "id_token": id_token,
                "scope": "openid email profile",
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @app.get("/userinfo")
    async def userinfo(request: Request) -> Response:
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        grant = state.access_tokens.get(presented.strip()) if scheme.lower() == "bearer" else None
        if grant is None or grant.expires_at <= clock():
            return JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        user = config.users[grant.sub]
        return JSONResponse({"sub": user.sub, "email": user.email, "name": user.name})

    return app
