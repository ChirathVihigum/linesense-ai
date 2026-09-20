# ADR-0008: Local development environment without Docker/Java

**Status:** Accepted — 2026-09-17

## Context

The plan's reference local environment is Docker Compose running
PostgreSQL, Keycloak (Java), the API, the worker, and the frontend dev
server (spec §4, §12). **This development environment has neither Docker
nor Java available.** Development still needs a real, non-mocked
PostgreSQL 16 + pgvector instance for domain/integration tests (the plan
forbids SQLite as a substitute for lease/locking/tenancy/migration
behavior — spec §13), and a working OIDC flow (ADR-0004) without Keycloak.

## Decision

Run PostgreSQL 16 + pgvector 0.8.6 as a **project-local cluster** managed
by `scripts/dev-db.sh`, entirely outside Homebrew's default/service-managed
cluster: data directory `.local/pgdata`, socket directory `.local/pgrun`,
port `55432`, `scram-sha-256` auth, bound to `127.0.0.1` only. `pgvector`
0.8.6 is built from source (`git clone --branch v0.8.6
https://github.com/pgvector/pgvector.git`) and installed into the same
Homebrew `postgresql@16` binary installation the project-local cluster runs
from, so `CREATE EXTENSION vector` resolves to `0.8.6` in both
`linesense_dev` and `linesense_test`. Identity uses a development-only OIDC
provider (ADR-0004) instead of a Keycloak container. Compose files
(`infra/compose/`) and the Keycloak realm configuration (`infra/identity/`)
are **planned deliverables of a later deployment task** (plan Task 26:
"Deployment artefacts and CI") for any environment that does have
Docker/Java — the `infra/compose/` and `infra/identity/` directories exist
today only as empty placeholders, with no Compose/Keycloak files written
yet. When Task 26 writes them, they will be validated **statically only**
(schema/lint checks, `make infra-check`) in this environment, never by
actually running them, because Docker/Java remain unavailable. Every
status report and README instruction distinguishes "works here, verified"
(the native `scripts/dev-db.sh` + dev IdP path) from "planned, not yet
written / validated only statically" (Compose/Keycloak).

## Consequences

- `make bootstrap`/`db-init`/`db-start`/`test-integration` work fully
  offline against a real PostgreSQL instance with no container runtime.
- The project-local cluster is strictly isolated from any other PostgreSQL
  installation on the machine: `scripts/dev-db.sh` never starts, stops, or
  modifies Homebrew's default cluster or any `brew services`-managed
  cluster, and `reset-test` refuses any target database other than
  `linesense_test`.
- A contributor with Docker/Java can instead use Compose/Keycloak once
  Task 26 has written those artefacts and they are exercised and confirmed
  in that environment; until then, README/docs/development-guide.md must not claim the
  Compose path has been written, let alone run.
- Production deployment still targets the Compose-based (or equivalent)
  configuration with a real OIDC provider — the dev IdP and project-local
  cluster are development/test-only, and `Settings`' production validator
  fails startup if production configuration still points at either.

## Alternatives considered

- **Require Docker/Java before any work proceeds**: rejected — blocks all
  development in this environment; the plan explicitly instructs
  completing independent work and marking the exact external blocker
  rather than stopping.
- **Use SQLite for local development**: rejected — the plan requires real
  PostgreSQL for lease/locking/tenancy/migration/concurrency behavior in
  tests; SQLite cannot exercise `FOR UPDATE SKIP LOCKED`, `tsvector`/GIN, or
  `pgvector`.
- **Skip identity entirely until Keycloak is available**: rejected — see
  ADR-0004; a working, testable OIDC flow is required for the walking
  skeleton and every later authenticated-flow test.
