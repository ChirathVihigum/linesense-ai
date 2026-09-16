# CLAUDE.md

LineSense AI: a FastAPI + PostgreSQL/pgvector + React decision-support app
for apparel factory operations (IT 3041 group assignment), built as a
modular monolith with a separate durable worker and four bounded AI agents
(planning, RM, IE, quality). Full spec: `LINESENSE_IMPLEMENTATION_PLAN.md`.
Binding schema/contract details: `docs/architecture/backend-contracts.md`.
Requirements/acceptance criteria: `docs/requirements.md`. Decisions:
`docs/adr/`. Progress log (read before resuming work): `docs/IMPLEMENTATION_STATUS.md`.

## Make commands

Existing (Task 1):
`bootstrap`, `db-init`, `db-start`, `db-stop`, `db-reset-test`, `migrate`,
`migration-check`, `lint`, `format`, `typecheck`, `test`, `test-integration`,
`test-all`, `docs-check`.

Planned, not yet implemented (added in later tasks):
`idp`, `seed`, `worker`, `contracts`, `contracts-check`, `datasets-check`,
`eval`, `dev`, `test-e2e`, `web-*`, `build`, `security`, `backup`,
`restore-check`, `perf`, `infra-check`, `screenshots`.

Backend commands run from `services/backend/` via `uv run` (Python 3.12,
dependencies added only with `uv add`). Frontend will live in `apps/web/`
and use `npm`.

## Critical invariants

- Table names, columns, role/permission names, error codes, protocol
  fields, and job functions must match `docs/architecture/backend-contracts.md`
  exactly.
- Local database: project-local cluster on port 55432 via
  `scripts/dev-db.sh`. Never start/stop/modify any other PostgreSQL
  cluster; never drop `linesense_dev`.
- Integration tests use real PostgreSQL (`linesense_test`), never SQLite or
  mocks for locking, leases, tenancy, migrations, or concurrency.
- LLMs never establish business truth or authorize writes. Stock, capacity,
  cycle-time metrics, quality eligibility, and lifecycle transitions are
  computed by deterministic code in `app/domain/`.
- Agents get read/compute tools only: no arbitrary SQL, shell, URL
  fetching, or tool creation. Limits: ≤4 tool calls/invocation, ≤12 model
  calls/run, ≤1 replan, ≤2 retries, 120-second run deadline.
- The agent protocol is a custom versioned HTTP/JSON protocol
  (`schema_version "1.0"`) — never call it A2A or MCP.
- Default Anthropic model is `claude-opus-5` (`LS_ANTHROPIC_MODEL`). No API
  key is available in this environment: **never claim live-LLM success
  without a recorded run**; the `fixture` provider is labelled as a test
  fixture everywhere it appears.
- Organization/factory scope is enforced on every path (API, worker tools,
  retrieval, citations, exports); inaccessible resources return 404,
  missing permission on an accessible scope returns 403.
- Self-approval is forbidden; stale/expired proposals are rejected with
  409; application locks capacity-slot and material-balance rows in
  deterministic (sorted id) order.
- No placeholder success handlers, TODO stubs for required behaviour,
  silent `except: pass`, hardcoded production secrets, fabricated test
  output, or mock numbers presented as live data.
- Every task: write failing tests first, run `make lint`/`make typecheck`
  and the relevant test targets, append a dated entry to
  `docs/IMPLEMENTATION_STATUS.md`, and commit with a conventional message.
  Never skip, weaken, or delete a failing test to get green.
