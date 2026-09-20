# Identity provider — Keycloak realm (Task 26)

This directory holds the Keycloak realm used by the Compose deployment's
local HTTPS demo (`infra/compose/docker-compose.yml`, `dev` profile). It has
never been imported into a running Keycloak: no Docker was available in the
environment that authored it, so it has only been validated statically
(JSON well-formedness, required client/redirect-URI fields, `make
infra-check` — see `scripts/validate-infra.py`).

## What's here

- `keycloak/linesense-realm.json` — realm `linesense`, one confidential
  client (`linesense-web`), and the ten demo identities from
  [`docs/architecture/backend-contracts.md` section 9](../../docs/architecture/backend-contracts.md#9-seeded-demo-identities-developmenttest-only).

## The `linesense-web` client

- Confidential (`publicClient: false`), authorization-code (`standardFlow`)
  only — no implicit flow, no direct access grants, no service account.
- PKCE required with S256 (`attributes.pkce.code.challenge.method: "S256"`).
- Redirect URI is exactly `https://localhost/auth/callback` — this must
  match `LS_OIDC_REDIRECT_URI` byte-for-byte (see
  `infra/compose/.env.compose.example`); Keycloak rejects any other
  redirect.
- `secret: "dev-oidc-client-secret"` is the same dev/demo placeholder
  already used by the repo's own `.env.example`
  (`LS_OIDC_CLIENT_SECRET=dev-oidc-client-secret`) — not a real secret,
  never rotate-worthy on its own, but still **must** be replaced before this
  realm file (or any realm derived from it) is ever imported into a
  real, internet-reachable Keycloak. `scripts/validate-infra.py` only
  flags high-risk secret *patterns* (cloud access keys, private-key
  headers, provider API-key prefixes); it does not — and cannot — replace a
  real secret-rotation review before production use.

## Demo users

All ten users from backend-contracts.md section 9 are imported with
`requiredActions: ["UPDATE_PASSWORD"]` and a `temporary: true` credential
(`demo-password`, the same convention as `LS_DEV_IDP_PASSWORD` in the
repo's own `.env.example`) — every one of them is forced to set a new
password on first login. LineSense's own authorization model
(`role_assignments`, `docs/architecture/backend-contracts.md` section 4) is
**not** carried in this realm: Keycloak here only proves *who* a user is
(email + a successful login); *what* they may do is decided entirely by the
application's own database, seeded separately by `app.seed` against
matching emails. This realm intentionally defines no Keycloak realm/client
roles.

**Do not import this file into a real production Keycloak.** It is a dev
convenience: obvious placeholder secrets, temporary passwords, no
production hardening (no LDAP/federation, no MFA policy, no rate-limit
tuning beyond Keycloak's `bruteForceProtected` default).

## Two hostnames, one realm, one known limitation

The Compose Caddy edge (`infra/proxy/Caddyfile`) fronts two hostnames on
its single published port 443:

- `https://localhost` — the LineSense app itself (SPA + `/api` + `/auth`).
- `https://idp.localhost` — Keycloak, reverse-proxied so **both** the
  browser (via the same published port) and the `api`/`worker` containers
  (via a Docker Compose network alias on the `web` service, see
  `docker-compose.yml`) resolve the realm's issuer identically. This
  matters because `app/auth/oidc.py` builds its discovery-document request
  directly from `LS_OIDC_ISSUER` (`<issuer>/.well-known/openid-configuration`)
  and there is no separate "internal" issuer override — whatever hostname
  names the issuer must be reachable, with the same discovery document, from
  both the browser and the backend container.

**Known limitation (local demo only):** `tls internal` (Caddyfile) mints
certificates from Caddy's own local CA, which the `api`/`worker` containers
do not trust by default. The backend's own discovery/token fetch to
`https://idp.localhost` would fail TLS verification until that CA is
trusted (e.g. by extracting Caddy's root certificate — `docker compose exec
web cat /data/caddy/pki/authorities/local/root.crt` — and adding it to the
backend image's trust store at build time). This is a demo-only artifact of
`tls internal`; **production does not have this problem**, because a real
deployment uses a real hostname behind Caddy's normal automatic HTTPS
(Let's Encrypt or another public CA), which the backend already trusts. See
`docs/operations/deployment.md` ("TLS" and "Known limitation").

## Production Keycloak (`start`, not `start-dev`)

Not modeled in `docker-compose.yml` at all — a real deployment should point
`LS_OIDC_ISSUER` at a separately managed, hardened Keycloak (or another
OIDC provider), run with `start` (not `start-dev`), a real external
database (`KC_DB=postgres`, its own credentials, not the LineSense
application database), `KC_HOSTNAME` set to the provider's real public URL,
proper `KC_PROXY_HEADERS` configuration for whatever reverse proxy fronts
it, MFA/password policy, and secrets rotation. `docs/operations/deployment.md`
documents this as prose guidance; there is no runnable Compose service for
it here.
