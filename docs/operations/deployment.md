# Deployment (Task 26)

**Docker was not available in the development environment that produced
these artefacts: they have been validated statically (`make infra-check`,
`scripts/validate-infra.py`) but never actually run.** Every claim below
about what a command does describes intent verified by reading the
relevant image/tool documentation and the artefacts themselves, not an
observed run.

This document covers the single-host Compose deployment described in
`LINESENSE_IMPLEMENTATION_PLAN.md` section 12 ("Reliability, efficiency,
and deployment"). It is written for a student/pilot deployment on one
appropriately sized VM or container host — **a single host is not highly
available**; the downtime/recovery limits below are the honest ceiling of
this design, not an enterprise SLA.

## 1. Verified image tags

Before any tag was pinned in a Dockerfile or Compose file, its existence
was confirmed against the registry's own API. Recorded here so the checks
can be re-run and re-verified independently.

| Image | Tag pinned | Verified (2026-09-20) via |
|---|---|---|
| `python` (backend build + runtime stages) | `3.12.14-slim-bookworm` | `curl -s https://hub.docker.com/v2/repositories/library/python/tags/3.12.14-slim-bookworm` |
| `node` (web build stage) | `26.9.0-alpine` | `curl -s https://hub.docker.com/v2/repositories/library/node/tags/26-alpine` (listing), confirmed `.../tags/26.9.0-alpine` -> 200 |
| `caddy` (web runtime stage) | `2.11-alpine` | `curl -s https://hub.docker.com/v2/repositories/library/caddy/tags?page_size=100` |
| `pgvector/pgvector` (Compose `db`, CI `postgres` service) | `0.8.6-pg16` | `curl -s https://hub.docker.com/v2/repositories/pgvector/pgvector/tags/0.8.6-pg16` |
| `quay.io/keycloak/keycloak` (Compose `keycloak`, `dev` profile) | `26.7.4` | `curl -s "https://quay.io/api/v1/repository/keycloak/keycloak/tag/?limit=20&onlyActiveTags=true"` |
| `uv` (backend builder stage, via PyPI, not an image) | `0.12.17` | `curl -s https://pypi.org/pypi/uv/json` (`info.version`) |

GitHub Actions used in `.github/workflows/ci.yml` were pinned the same
way — checked against the action repository's own tags, not assumed from
memory, since this environment's knowledge of "current" majors predates
2026-09-20:

| Action | Pinned | Verified via |
|---|---|---|
| `actions/checkout` | `v7.0.1` | `curl -s https://api.github.com/repos/actions/checkout/tags` |
| `actions/setup-node` | `v7.0.0` | `curl -s https://api.github.com/repos/actions/setup-node/tags` |
| `astral-sh/setup-uv` | `v10.1.0` | `curl -s https://api.github.com/repos/astral-sh/setup-uv/tags` |
| `actions/upload-artifact` | `v7.0.1` | `curl -s https://api.github.com/repos/actions/upload-artifact/tags` |

`scripts/validate-infra.py` re-checks (statically, from the committed
files) that no image tag is `latest` and that every `uses:` action
reference is pinned to an explicit version, not a floating ref
(`@main`/`@latest`/etc.) — see `check_image_tags_not_latest` and the
`uses:` loop in `check_workflow`.

## 2. Artefacts and what they do

| Path | Purpose |
|---|---|
| `services/backend/Dockerfile` | Multi-stage backend image (uv-resolved venv, then a non-root runtime with `app/` + `migrations/` + `alembic.ini` — deliberately **not** `devtools/`, the dev-only OIDC provider, ADR-0004). One image, three entrypoint modes selected by the first argument: `api`, `worker`, `migrate` (`docker-entrypoint.sh`). |
| `apps/web/Dockerfile` | Builds the Vite/React SPA (Node pinned from `apps/web/.nvmrc`), then serves the static output behind Caddy, which also reverse-proxies `/api` and `/auth` and blocks `/internal/*`. |
| `infra/proxy/Caddyfile` | The edge: TLS (`tls internal` for the local demo), security headers (HSTS/CSP/X-Frame-Options/etc.), `/internal/*` -> 404, `/api` and `/auth` -> the API container, everything else -> the SPA with client-routing fallback, plus a second site block fronting Keycloak. |
| `infra/compose/docker-compose.yml` | `db`, `migrate` (one-shot), `api`, `worker`, `web` (the only service publishing a host port, 443), and `keycloak` (`dev` profile only). A private `internal` network carries everything else. |
| `infra/compose/.env.compose.example` | Every `LS_*`/Postgres/Keycloak variable Compose needs, with obvious dev/demo placeholder values (same convention as the repo's own `.env.example`) — copy to `.env.compose` (git-ignored) and never commit real values. |
| `infra/compose/db-init/01-roles.sh` | Runs once (Postgres `docker-entrypoint-initdb.d` convention) to create `linesense_owner`/`linesense_app`, the application database, and the `vector`/`pg_trgm` extensions — mirrors `scripts/dev-db.sh`. |
| `infra/identity/keycloak/linesense-realm.json` + `infra/identity/README.md` | Realm `linesense`, confidential client `linesense-web` (PKCE S256 required, exact redirect `https://localhost/auth/callback`), the ten demo identities from [backend-contracts.md section 9](../architecture/backend-contracts.md#9-seeded-demo-identities-developmenttest-only) with temporary, reset-forced passwords. |
| `.github/workflows/ci.yml` | `backend` (lint/typecheck/test/test-integration/migration-check/datasets-check/docs-check/infra-check, with a pgvector service container), `contracts` (`make contracts-check`), `web` (lint/typecheck/test/build), `security` (`make security`), and a manual-only `eval` (`make eval`, uploads results). No `e2e` job — browser end-to-end (Task 24) was dropped. |
| `scripts/validate-infra.py` (`make infra-check`) | The static substitute for actually running any of the above: parses the Compose file, the CI workflow, and the realm JSON (PyYAML/`json`, already a backend dependency — no new package was needed), checks required services/jobs/keys, that no secret-shaped literal is present, that `/internal` is blocked in the Caddyfile, and that no image tag is `latest`. |

## 3. Deployment sequence (spec section 12)

1. **Build and scan immutable images.** `docker compose -f infra/compose/docker-compose.yml build` (each service image is content-addressed by its build; tag releases explicitly, e.g. `LS_IMAGE_TAG=2026.09.20` in `.env.compose`). Scanning is `make security`'s dependency-audit step (Task 25) plus whatever container scanner the host CI/registry provides — not modeled as a Compose service here.
2. **Back up.** `pg_dump` (or the managed Postgres provider's own backup) against the `db` service **before** running migrations. See section 6.
3. **Run migrations exactly once**, as a release job — not competitively in every server worker (spec section 12): `docker compose run --rm migrate`. This is the `migrate` service's entire job (`alembic upgrade head` against `LS_MIGRATION_DATABASE_URL`, the `linesense_owner` role) and it exits on completion; `api`/`worker` `depends_on: migrate: condition: service_completed_successfully`, so a normal `docker compose up` already sequences this correctly.
4. **Deploy API/worker/assets.** `docker compose --profile dev up -d` for the local HTTPS demo (add `keycloak`); a real deployment omits `--profile dev` and points `LS_OIDC_ISSUER` at a separately managed identity provider (`infra/identity/README.md`).
5. **Readiness checks.** `GET /api/health/live` and `/api/health/ready` (the `api` service's own Docker healthcheck already polls `/api/health/live`); confirm `worker` is heartbeating (`app/jobs`) before considering the deploy complete.
6. **Authenticated smoke flow.** Log in as one seeded demo identity (`infra/identity/keycloak/linesense-realm.json`, dev profile) through `https://localhost`, load the factory overview dashboard, and confirm at least one read-scoped API call succeeds — this is the same shape of check `docs/evaluation/methodology.md` (Task 19) will eventually automate, not a new mechanism.
7. **Observe.** API latency/errors, queue age, task retries/lease expiry, run failures, model latency/tokens/cost, invalid agent outputs, denied permissions, missing evidence, and stale approvals (spec section 12) — no metrics/tracing stack is bundled in this Compose file; wiring one is future work, noted in limitations below.

## 4. Environment variables

Full reference: `services/backend/app/settings.py` (`Settings`, prefix
`LS_`) and the repo root `.env.example`. Compose-specific defaults and
comments live in `infra/compose/.env.compose.example`. The table below
covers only the variables a Compose deployment must actually decide.

| Variable | Local demo default | Production requirement |
|---|---|---|
| `LS_ENVIRONMENT` | `development` | `production` — activates `Settings`' hardening validator (below) |
| `LS_DATABASE_URL` / `LS_MIGRATION_DATABASE_URL` | `db:5432/linesense`, dev-looking passwords | Real, unique, rotated credentials; consider managed Postgres |
| `LS_SESSION_SECRET` / `LS_SERVICE_TOKEN` | `dev-*` placeholders | >= 32 chars, must **not** contain `dev-` (enforced) |
| `LS_PUBLIC_ORIGIN` | `https://localhost` | The real public origin, `https://` required (enforced) |
| `LS_OIDC_ISSUER` / `LS_OIDC_CLIENT_SECRET` / `LS_OIDC_REDIRECT_URI` | The bundled `dev`-profile Keycloak | A separately managed, hardened IdP; issuer and redirect must be `https://` (enforced); client secret must not contain `dev-` (enforced) |
| `LS_LLM_PROVIDER` | `fixture` (deterministic test double, never live) | `fixture` is **forbidden** in production (enforced); `anthropic` requires `LS_ANTHROPIC_API_KEY` (enforced) |
| `LS_FORWARDED_ALLOW_IPS` | `172.28.0.0/24` (the Compose `internal` network) | Whatever CIDR the real reverse proxy sits on — never `*` |
| `LS_DOCUMENT_STORAGE_DIR` / `LS_WORKER_ALIVE_DIR` | Container paths on named volumes | Same, or real object storage once that migration happens (not yet built) |

`Settings._validate_production_hardening` (`services/backend/app/settings.py`)
enforces the "production requirement" column at process startup — a
misconfigured production deployment fails fast rather than starting
insecurely.

## 5. TLS

- **Local demo:** `infra/proxy/Caddyfile` uses `tls internal`, minting
  certificates from Caddy's own local CA. Browsers on the host machine
  will show a certificate warning (or need that local CA trusted) — this
  is expected and is a demo-only shortcut.
- **Production:** replace `tls internal` with a real hostname; Caddy's
  automatic HTTPS then obtains a publicly trusted certificate (e.g. Let's
  Encrypt) with no configuration change beyond the site address. See
  `infra/identity/README.md` for the specific limitation this shortcut
  creates for the bundled dev Keycloak (and why production does not have
  that problem).
- Digest pinning (`image@sha256:...` instead of `image:tag`) is a
  production recommendation, not applied here: tags were individually
  verified to exist (section 1) for reproducibility during development, but
  a production release pipeline should re-pin by digest at build time so a
  tag cannot be silently repointed upstream.

## 6. Secrets handling

- Real secrets are never committed. `.env.compose` (like `.env`) is
  git-ignored; only the `.example` file with placeholders is tracked.
- `linesense-realm.json`'s client secret and demo-user passwords are
  intentionally obvious, literal placeholders (documented in
  `infra/identity/README.md`) — this realm file is a dev/demo convenience
  and must never be imported into a real, internet-reachable Keycloak
  without regenerating every credential in it.
- `scripts/validate-infra.py` (`make infra-check`) scans the Compose file,
  Caddyfile, realm JSON, both Dockerfiles, and the CI workflow for
  high-risk secret-shaped literals (cloud access keys, PEM private-key
  headers, known LLM-provider API-key prefixes) — it does not, and cannot,
  replace a real secret-rotation review before production use.
- Backups (below) must be encrypted at rest and access-controlled
  separately from the application database credentials.

## 7. Rollback limits

- Expand/contract migrations are the safe rollback path (spec section
  12): a destructive `alembic downgrade` is **not** a default rollback
  plan, because application rollback requires database compatibility with
  the *previous* code version, and a down-migration can destroy data an
  in-flight write already depends on.
- Practical rollback = redeploy the previous image tag (`LS_IMAGE_TAG`)
  against the *same*, forward-migrated schema, provided that schema
  version remains compatible with the previous code (expand/contract
  discipline is what makes this true). If a migration was not
  backward-compatible, rollback instead means restoring from the
  pre-migration backup (section 6/8) into a separate environment and
  reconciling any writes made since — this is slower and lossier, which is
  exactly why expand/contract is the default discipline, not a nice-to-have.
- Document storage (`LS_DOCUMENT_STORAGE_DIR`, a named volume in Compose)
  is versioned independently of the database; a schema rollback does not
  retroactively un-process a document, so a genuine rollback should also
  reconcile `document_versions` rows against what is actually present in
  storage (spec section 12: "reconciling private document files with
  database references").

## 8. Backup / restore objectives (targets, not guarantees)

Per spec section 12 ("Demo backup objective"): **daily encrypted backups,
a recovery point objective (RPO) of up to 24 hours, and a restore exercise
targeting a recovery time objective (RTO) within 4 hours.** These are
planning objectives to be measured, not a contractual SLA — a commercial
deployment needs a stronger design and contractual review before either
number can be promised to a customer.

A single Compose host is, by construction, not highly available: there is
one `db` container, one volume, and no automatic failover. The honest
scope of this design is a single-host pilot/demo, matching spec section
12's own framing ("a single host is not highly available; document its
downtime/recovery limits rather than claiming enterprise resilience").

## 9. Known limitation — local HTTPS demo TLS trust

Documented in full in `infra/identity/README.md`. Summary: the backend's
own OIDC discovery fetch is built directly from `LS_OIDC_ISSUER`
(`app/auth/oidc.py`), so it must trust whatever certificate authority
signed that issuer's TLS certificate. `tls internal` (section 5) mints an
untrusted-by-default local CA, so the `api`/`worker` containers would need
that CA added to their trust store before the demo login flow could
actually complete end to end. This is a `tls internal`-specific,
demo-only artefact; a production deployment with a real hostname does not
have this problem, because the backend already trusts public CAs.

## 10. Dependencies on other tasks

Two CI jobs in `.github/workflows/ci.yml` call Makefile targets that this
task did not create and that may not exist yet in every commit on this
branch:

- `security` -> `make security` (Task 25: `scripts/secret-scan.sh`,
  `scripts/dependency-audit.sh`, `docs/security/scan-results.md`).
- `eval` (manual `workflow_dispatch` only) -> `make eval` (Task 19:
  `app.evaluation`, `docs/evaluation/results/`).

Both jobs are wired now, correctly, so they start working the moment
those targets land — see `docs/IMPLEMENTATION_STATUS.md` for whether they
have, as of any given commit.

## 11. Limitations

- **Never run.** No Docker in this environment; every artefact here is
  statically validated (`make infra-check`) only. Treat this as a
  carefully reasoned draft, not a tested deployment.
- No metrics/tracing/log-aggregation stack is bundled (section 3, step 7)
  — structured JSON logs (`structlog`, per backend-contracts.md section 1)
  are the only observability surface Compose provides out of the box.
  Actually collecting them (Loki, CloudWatch, etc.) is host/provider-specific
  and out of scope here.
- Production Keycloak (`start`, real external DB, MFA/password policy) is
  documented as prose only (`infra/identity/README.md`); there is no
  runnable Compose service for it, by design (a real deployment should use
  a separately managed, hardened identity provider, not one bundled into
  the application's own Compose file).
- Digest pinning, container image scanning, and backup automation are
  documented as production recommendations/dependencies (sections 5, 6, 10)
  rather than implemented, since they either require Docker to exercise or
  belong to Task 25.
