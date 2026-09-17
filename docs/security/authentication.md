# Authentication, sessions and CSRF

This document describes how LineSense authenticates browser users and protects cookie-authenticated
requests. The decision record is [ADR-0004](../adr/0004-oidc-server-sessions-and-dev-idp.md); the
binding interface is [backend contracts §4–§5](../architecture/backend-contracts.md).

## Components

| Piece | Location | Responsibility |
|---|---|---|
| OIDC relying party | `services/backend/app/auth/oidc.py` | Authlib client `linesense`, discovery via `LS_OIDC_ISSUER/.well-known/openid-configuration`, scope `openid email profile`, PKCE `S256` |
| Auth routes | `services/backend/app/auth/routes.py` | `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout` |
| Server sessions | `services/backend/app/auth/sessions.py` | opaque tokens, `sessions` table, principal loading |
| CSRF middleware | `services/backend/app/auth/csrf.py` | origin + token check for unsafe requests |
| Policy / scope | `services/backend/app/auth/policy.py`, `scope.py` | role → permission matrix, org/factory scoping |
| Dependencies | `services/backend/app/api/deps.py` | `get_principal`, `get_optional_principal`, `require_idempotency_key` |
| Development IdP | `services/backend/devtools/dev_oidc/` | local OIDC provider for development and tests only |

## Login flow

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as LineSense API
    participant IdP as OIDC provider
    participant DB as PostgreSQL

    B->>API: GET /auth/login?next=/orders/42
    API->>IdP: GET /.well-known/openid-configuration (cached)
    API-->>B: 302 to IdP /authorize (state, nonce, code_challenge S256)<br/>Set-Cookie ls_oidc (signed, 10 min, path /auth)
    B->>IdP: GET /authorize, then POST credentials
    IdP-->>B: 302 to /auth/callback?code&state
    B->>API: GET /auth/callback?code&state (+ ls_oidc)
    API->>API: check state against ls_oidc
    API->>IdP: POST /token (code, code_verifier, client auth)
    IdP-->>API: id_token (RS256), access_token
    API->>IdP: GET /jwks (cached)
    API->>API: check signature, iss (= discovery issuer), aud (= client id), exp, nonce
    API->>DB: upsert users by (iss, sub); revoke old session; insert session; audit auth.login
    API-->>B: 302 to next<br/>Set-Cookie ls_session (HttpOnly)
    B->>API: GET /api/v1/me
    API-->>B: user, organization, factories + roles/permissions, csrf_token
```

- `next` is accepted only if it is a relative path with exactly one leading `/`: no scheme, no
  host, no backslash and no control characters. Anything else becomes `/`. Redirects are absolute
  URLs on `LS_PUBLIC_ORIGIN`.
- Authlib stores the handshake data (state, nonce, PKCE verifier) and the `next` path in the
  `ls_oidc` cookie. Starlette's `SessionMiddleware` signs that cookie with `LS_SESSION_SECRET`. It
  lasts 10 minutes, uses `SameSite=Lax` and path `/auth`, and is `Secure` in production. It carries
  no application session.
- ID-token claims are enforced explicitly: `iss` must equal the discovery document's `issuer`,
  and `aud` must contain `LS_OIDC_CLIENT_ID`. Authlib's own `azp` check alone would accept a
  foreign `aud` when `azp` names this client. `sub` is required. Authlib and joserfc also check
  `exp` and `iat` (with 120 s leeway), `nonce` and the RS256 signature against the published
  JWKS.
- The issuer in the discovery document must match `LS_OIDC_ISSUER`, ignoring a trailing slash.
  If it doesn't, `/auth/login` returns `503 SERVICE_UNAVAILABLE` and the callback fails.
  `users.issuer` always stores the normalized issuer (no trailing slash), from
  `app.auth.oidc.normalize_issuer`. The seed and test helpers store it the same way, so rows
  created by the callback, the seed and the tests all match on `(issuer, subject)`.
- The callback never creates memberships. Users are provisioned by an administrator or the seed.
  An identity with no active membership still gets a session, but `/api/v1/me` and every protected
  route return `403 FORBIDDEN` with the message "No LineSense membership".
- The callback fails when the state doesn't match, the IdP rejects the token exchange, a signature
  or claim check fails, or the user is inactive. On failure it redirects to
  `/login?error=auth_failed` and creates no session. The same applies when the provider's
  metadata is unusable, for example when it has no `jwks_uri`. The failure is audited as `auth.login` with
  outcome `FAILED` when the user and their organization are known. Otherwise it is only logged.
- The provider's `id_token` and `access_token` are used only during the callback. They are never
  stored, logged or sent to the browser.

## Sessions

| Property | Value |
|---|---|
| Cookie | `ls_session` holds a random token from `secrets.token_urlsafe(32)` (256 bits) |
| Attributes | `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when `LS_ENVIRONMENT=production`, `Max-Age` = session lifetime |
| Storage | Only `sha256(token)` in hex is stored, in `sessions.token_hash` |
| Lifetime | Absolute: `LS_SESSION_MAX_AGE_SECONDS` (default 8 hours) from login. It isn't extended when the session is used |
| Activity | `last_seen_at` is updated at most once a minute |
| Rotation | Each successful login revokes any session named by the incoming cookie, then issues a new token |
| Logout | `POST /auth/logout` (CSRF-protected) sets `revoked_at`, clears the cookie and audits `auth.logout`. It returns `204` |
| Rejection | Unknown, expired or revoked tokens return `401 UNAUTHENTICATED`. Inactive users also return `401` |

Each session row also has a `csrf_token` from `secrets.token_urlsafe(32)`, which
`GET /api/v1/me` returns.

## CSRF protection

`CsrfMiddleware` is pure ASGI. It runs inside the trace-id, security-header and handshake-cookie
middleware, and before routing. It checks every `POST`, `PUT`, `PATCH` and `DELETE` (anything
except `GET`, `HEAD` and `OPTIONS`) to `/api/...` and to `/auth/logout`:

1. **Origin.** The `Origin` header, or the origin of `Referer` when `Origin` is absent, must equal
   `LS_PUBLIC_ORIGIN` exactly. A missing header, a mismatch or `Origin: null` fails the check.
2. **Token.** If `ls_session` names a live session, `X-CSRF-Token` must equal that session's
   `csrf_token`. The comparison uses `hmac.compare_digest`.

If either check fails, the response is `403` with code `CSRF_FAILED` in the standard error body,
including `trace_id`. A request with no live session has no cookie authority to abuse, so after the
origin check it goes on to the route, which returns `401`. That way an expired session produces the
normal "sign in again" response. The middleware keeps the session record it resolved on the request
scope, so `get_principal` doesn't look it up again. `/internal/...` routes aren't covered: they
authenticate with a bearer service token, not cookies.

`SameSite=Lax` on `ls_session` is a second layer of defence. It isn't relied on alone.

## Security headers

`SecurityHeadersMiddleware` adds these headers to every HTTP response, including `500` responses
built by the unhandled-exception handler:

- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: same-origin`
- `X-Frame-Options: DENY`
- `Cache-Control: no-store`, only for paths under `/api` and `/auth`

## Authorization

`Principal.roles_by_factory` maps each factory id to that factory's roles. The `None` key holds
org-wide roles, which apply to every factory in the organization. `require(principal, permission,
factory_id)` raises `403 FORBIDDEN`. `load_scoped` returns `404 NOT_FOUND` for rows in another
organization or in a factory where the principal has no role. It returns `403` when the principal
has a role in that factory but lacks the permission. The permission matrix is in
[backend contracts §4](../architecture/backend-contracts.md). `org_admin` has no implicit business
authority.

## Audit and idempotency

- `app.audit.service.record_audit` writes `audit_events` in the caller's transaction. Before
  writing, it replaces with `"[REDACTED]"` any `before`/`after` value (at any depth) stored under a
  credential key. Keys are normalized first: camelCase and `-` become `_`, and case is ignored.
  A key is a credential key if it is one of `token`, `password`, `secret`, `api_key`,
  `authorization`, `cookie`, `set_cookie`, `credentials` or `private_key`, or if it ends in
  `_token`, `_password`, `_secret`, `_api_key` or `_private_key`. Usage counters such as
  `input_tokens` or `token_count` are kept. A denied request's transaction is rolled
  back, so `audit_denied` commits its `DENIED` event in a separate transaction.
- `app.idempotency.service.begin` / `finish` implement `Idempotency-Key` handling (8–128
  characters). Keys are scoped by organization, actor, operation and key, and are kept for 24 hours.
  The payload is compared by the SHA-256 of its canonical JSON. In that JSON every value is
  tagged with its type, so `Decimal("1")`, `"1"` and `1` never collide, and unsupported types
  raise `TypeError`. A matching retry replays the stored
  response. A different payload returns `409 IDEMPOTENCY_KEY_REUSED`. A duplicate that arrives
  while the first request is still running waits on the first transaction's row lock, because the
  insert uses `INSERT ... ON CONFLICT DO NOTHING` and then re-reads the row.

## Development identity provider

Start it with `make idp`, which runs `uv run python -m devtools.dev_oidc`. It listens on the host
and port of `LS_OIDC_ISSUER` (default `http://127.0.0.1:8090`) and offers discovery, JWKS,
`/authorize` (HTML form), `/token` and `/userinfo`. Its users are the ten demo identities in
`devtools/dev_oidc/users.json`, from contracts §9. They share the password `LS_DEV_IDP_PASSWORD`.

What it enforces:

- It exits with code 2 unless `LS_ENVIRONMENT` is `development` or `test`.
- The client ID must match and the redirect URI must match exactly.
- `response_type` must be `code`.
- PKCE `S256` is required.
- `state` and `nonce` are required.
- The password check is constant-time.
- Codes are single-use and expire after 60 seconds. Any attempt to redeem a code consumes it.
- ID tokens are signed with RS256 and last 300 seconds.
- All HTML output is escaped, and every page shows the banner "Development identity provider — not
  for production".

**Limitations, and why production must use a real IdP.** The development IdP has:

- one shared password and no per-user credentials, MFA, lockout or rate limiting;
- users read from a static file;
- codes and access tokens kept in process memory, which a restart loses;
- a signing key generated at every start, with no rotation or persistence;
- no refresh tokens, consent, logout endpoint or session management;
- no TLS.

It exists only so development and tests can run a real OIDC round trip without Docker or Java.
Production must use a real provider over HTTPS, such as the planned Keycloak realm (ADR-0004). The
settings validator refuses to start production when `LS_OIDC_ISSUER` or `LS_OIDC_REDIRECT_URI` is
not `https://`, or when `LS_OIDC_CLIENT_SECRET` contains a development value (`dev-`).

## Tests

- `tests/unit/test_policy.py`: every role/permission pair, plus org-wide and per-factory scoping.
- `tests/unit/test_dev_oidc.py`: the IdP endpoints, PKCE, code reuse and expiry, and a check that
  `users.json` matches the identity list.
- `tests/unit/test_http_hardening.py`: security headers, pagination, `next` sanitising, origin
  parsing and production settings.
- `tests/integration/test_auth_flow.py`: the full login against the development IdP. That IdP runs
  under uvicorn in a background thread, and Authlib fetches discovery, JWKS and tokens from it over
  real HTTP. The tests also reject tampered ID tokens (wrong `aud` with a matching `azp`, wrong
  `iss`, expired, wrong nonce, missing `aud`, forged signature). They produce these through the
  IdP's in-process `DevIdpHooks.id_token_tamper`, which can't be reached over HTTP and which
  `python -m devtools.dev_oidc` never sets.
- `tests/integration/test_csrf.py`, `test_scope.py`, `test_idempotency.py` and `test_audit.py`.
