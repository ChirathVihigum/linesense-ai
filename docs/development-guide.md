# Development guide

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
  and only the tests relevant to the change (see the heat policy below), append a dated entry to
  `docs/IMPLEMENTATION_STATUS.md`, and commit with a conventional message.
  Never skip, weaken, or delete a failing test to get green.

## Machine heat and workload policy (overrides test/build guidance above)

Protect this MacBook from unnecessary heat and sustained heavy workloads.

- Default to lightweight local work: editing, inspection, and focused checks.
- Before running a command, consider its CPU, GPU, memory, and duration.
- Run only the tests relevant to the change (`uv run pytest <path>::<test>`). Do not repeat successful tests unless something changed.
- Run only one resource-heavy job at a time across all agents (test runs, builds, `npm install`, Playwright, seeding, evaluation). Do not run parallel test suites or builds. Wrap heavy commands with `scripts/heavy-job.sh <command>`, which serializes them through a lock under `.local/`.
- Do not start local model training, large inference jobs, benchmarks, load tests (`make perf`), evaluations (`make eval`), or full test suites (`make test`, `make test-integration`, `make test-e2e`, `make test-all`) without asking the user first.
- Prefer an approved development server or CI for heavy work. Do not move workloads onto production or create paid resources without permission.
- Use supported worker limits and reduced concurrency (e.g. worker `--concurrency 1`, pytest without `-n`). Avoid background watchers, polling loops, and jobs left running unnecessarily; stop any dev server/worker/IdP you started when done.
- If the user reports heat, stop your heavy jobs promptly. Check for any child processes you started and report what remains running. Do not stop unrelated applications.
- If macOS reports thermal pressure or throttling (`pmset -g therm`), pause heavy work. Resume only after conditions improve; ask the user before restarting a workload that caused overheating.
- Do not invent a universal safe temperature or claim the Mac is cool without evidence. If thermal readings are unavailable, say so.
- Never disable macOS thermal protections or change fan controls.
- If a required check is too heavy to run safely, report it as pending and suggest running it in CI. Do not claim it passed.

Keep status updates short: what is running, what was stopped, and what still needs checking.
