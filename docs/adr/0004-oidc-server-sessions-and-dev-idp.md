# ADR-0004: OIDC + PKCE with opaque server sessions, dev-only IdP

**Status:** Accepted — 2026-09-17

## Context

The plan requires OIDC authorization-code flow with PKCE, provider tokens
kept server-side, and an opaque application session cookie (spec §11).
The plan's reference identity provider is Keycloak, run as a local Compose
container (spec §4). This development environment has **no Docker and no
Java available**, so a Keycloak container cannot be run or verified here.
The team still needs a working, testable OIDC flow (login, callback,
logout, session issuance) to build and test authentication/authorization
against, including the two-user separation-of-duties scenarios (spec §11).

## Decision

Implement the OIDC client side exactly as planned: authorization code +
PKCE + state + nonce via Authlib, exchange for an opaque high-entropy
session token (`sessions.token_hash = sha256(token)`), CSRF protection via
a double check — the `X-CSRF-Token` header must equal the session's
`csrf_token` **and** the request's `Origin`/`Referer` must match
`LS_PUBLIC_ORIGIN` (`docs/architecture/backend-contracts.md` §5). Because
Keycloak cannot run locally, add a minimal, development-only OIDC provider
at `services/backend/devtools/dev_oidc` that implements just enough of the
protocol (authorization endpoint, token endpoint, JWKS, the seeded demo
identities in `docs/architecture/backend-contracts.md` §9) to exercise the
full client-side flow end-to-end, including browser E2E tests. The
Keycloak realm configuration is kept under `infra/identity/` for Compose-
based environments where Docker/Java are available, but it is **not**
verified in this environment — that is recorded as an explicit gap, not a
claimed pass.

## Consequences

- Every session/CSRF/authorization test in this environment runs against a
  real OIDC round trip (not a mocked identity), because the dev IdP speaks
  the real protocol shapes the Authlib client expects.
- The dev IdP must never be reachable or used outside `development`/`test`
  (`LS_ENVIRONMENT`); production startup must fail if it is configured as
  the issuer in production, matching the plan's "no live-provider claim
  without evidence" discipline applied here to identity as well as LLMs.
- The Keycloak realm/Compose path is unverified until a Docker+Java
  environment is available; this is tracked as a known limitation in
  `docs/IMPLEMENTATION_STATUS.md`, not silently dropped from scope.
- Cookie/session mechanics (Secure/HttpOnly/SameSite, rotation on login,
  revocation on logout) are identical regardless of which IdP issued the
  identity, so switching to Keycloak later requires no session-layer
  changes.

## Alternatives considered

- **Mock authentication (fake principal, no OIDC round trip)**: rejected —
  would not exercise the actual Authlib validation path (issuer, audience,
  signature, PKCE) the security requirements depend on, and would make the
  E2E login tests meaningless.
- **Skip Docker-dependent identity work entirely until a Docker host is
  available**: rejected — blocks all authenticated-flow development and
  testing for the whole assignment; the dev IdP unblocks it now while
  keeping the Keycloak path documented for later verification.
- **Use a public/hosted OIDC test provider**: rejected — introduces an
  external network dependency for every test run and cannot host the
  project's specific seeded demo identities/roles.
