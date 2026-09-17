# LineSense AI: implementation status

This file is a running, dated log of what has actually been built, tested, and verified,
kept alongside `LINESENSE_IMPLEMENTATION_PLAN.md` and `docs/architecture/backend-contracts.md`.
It is updated at the end of every task.

## Phase checklist

Phases from `LINESENSE_IMPLEMENTATION_PLAN.md` section 14 ("Implementation sequence and
milestones"):

- [x] Phase 0: requirements and contracts — domain glossary, policies, threat model, data
      contracts, ADRs, official dependency check, synthetic fixtures.
      **Documented and implemented**: the formulas in `docs/architecture/formulas.md` and
      its four reference fixtures are now backed by deterministic code and unit tests
      (Task 4, `app/domain/`); fixtures 5 and 6 (concurrency/staleness) remain database-backed
      and are deferred to Task 13.
- [ ] Phase 1: walking skeleton — repository, Compose, migrations, OIDC/session, membership
      policies, CI, order list/create/detail, generated client
- [ ] Phase 2: durable two-agent slice — jobs/leases, orchestrator, RM and planning agents,
      real LLM adapter, typed HTTP protocol, evidence and approvals
- [ ] Phase 3: retrieval and NLP — document pipeline, hybrid retrieval, citations, entity
      extraction/classification, first evaluation
- [ ] Phase 4: complete domain scope — IE and quality agents, observations/inspections, all
      dashboard screens, quality holds/releases
- [ ] Phase 5: hardening and deployment — concurrency, uploads, injection testing, scans,
      performance, backup restore, hosted environment
- [ ] Phase 6: assessment package — final report, measured comparison, pricing, Gen AI video,
      repo polish, viva practice

## Environment

- macOS (Darwin), no Docker, no Java available in this environment.
- PostgreSQL: Homebrew `postgresql@16` (16.15) binaries, used to run a **project-local**
  cluster (data dir `.local/pgdata`, port 55432, socket dir `.local/pgrun`), managed
  exclusively by `scripts/dev-db.sh`. The Homebrew default cluster/`brew services` is never
  started, stopped, or modified.
- pgvector 0.8.6 was compiled from source and installed into the Homebrew `postgresql@16`
  installation, so `CREATE EXTENSION vector` (default_version `0.8.6`) works in any cluster
  built from those binaries, including the project-local one.
- Python 3.12 (pinned via `.python-version` / `pyproject.toml`), dependency management via
  `uv` (0.11.17).
- No LLM API key is configured (`LS_ANTHROPIC_API_KEY` is blank). `LS_LLM_PROVIDER=fixture`
  in development/test. No live-provider call has been made or claimed in this environment.

## Log

### 2026-09-17 — Task 1: development infrastructure, settings, database engine, health checks

**Built:**

- `scripts/dev-db.sh`: idempotent `init`/`start`/`stop`/`status`/`psql`/`reset-test` for a
  project-local PostgreSQL 16 cluster (`.local/pgdata`, port 55432, `127.0.0.1` only,
  `scram-sha-256` auth). `init` creates superuser `linesense_super`, roles
  `linesense_owner`/`linesense_app`, databases `linesense_dev`/`linesense_test`, and in each:
  `vector` + `pg_trgm` extensions, `GRANT CONNECT`/`GRANT USAGE ON SCHEMA public` to
  `linesense_app`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC`. `reset-test` refuses any
  database name other than `linesense_test`.
- `Makefile` (root): `bootstrap`, `db-init`/`db-start`/`db-stop`/`db-reset-test`, `migrate`,
  `migration-check`, `lint`, `format`, `typecheck`, `test`, `test-integration`, `test-all`.
- `.env.example` (root): every `LS_*` setting documented with safe development defaults.
- `app/settings.py`: `Settings` (pydantic-settings, `env_prefix="LS_"`, reads `.env` from the
  repo root and `services/backend/.env`), `get_settings()` (`lru_cache`d). A `model_validator`
  enforces production hardening (session secret/service token length + no `dev-`, `https://`
  public origin, `llm_provider != "fixture"`, `anthropic_api_key` required when
  `llm_provider == "anthropic"`).
- `app/logging.py`: `configure_logging(settings)` — structlog JSON renderer, ISO timestamps,
  `trace_id` from `trace_id_var`, and a processor dropping `authorization`, `cookie`,
  `api_key`, `token`, `password`, `prompt`, `document_text` keys.
- `app/api/middleware.py`: `TraceIdMiddleware` (pure ASGI) + `trace_id_var` — validates
  inbound `X-Request-Id` as a UUID or generates one, sets `scope["state"]["trace_id"]`, echoes
  the response header.
- `app/api/errors.py`: `AppError` + handlers for `AppError`, `RequestValidationError` (422
  `VALIDATION_ERROR` with dotted `field_errors`, `body`/`query` prefix stripped), Starlette
  `HTTPException` (401/403/404/405 mapped), and unhandled `Exception` (500 `INTERNAL_ERROR`,
  logged with traceback, never echoes exception text). All responses use the contract error
  body shape from `docs/architecture/backend-contracts.md` section 5.
- `app/db/base.py`: `Base` (`DeclarativeBase`) with the specified naming convention.
- `app/db/session.py`: `get_engine(url)` (cached, `pool_pre_ping=True`),
  `get_session_factory(url=None)` (`async_sessionmaker(expire_on_commit=False)`),
  `get_db_session(request)` (FastAPI dependency; commits on success, rolls back on exception;
  resolves the URL from `request.app.state.settings` so tests can point it at the test DB).
- `app/api/health.py`: `GET /api/health/live`, `GET /api/health/ready` (`SELECT 1` +
  `pg_extension` version check; 503 `SERVICE_UNAVAILABLE` via the standard error contract on
  failure).
- `app/main.py`: `create_app(settings=None)` wiring logging, `TraceIdMiddleware`, exception
  handlers, the health router, and `app.state.settings`.
- Alembic scaffolding (`alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`,
  `migrations/versions/.gitkeep`): async engine via `run_sync`, URL resolved from
  `-x db_url=` or `Settings.migration_database_url`, `app.db.models` imported only if it
  exists (`importlib.util.find_spec` guard — that package doesn't exist until Task 3),
  `compare_type=True`.
- `tests/conftest.py`: `settings` (session-scoped, `environment="test"`, URLs from
  `LS_TEST_DATABASE_URL`/`LS_TEST_MIGRATION_DATABASE_URL` or their `.env.example` defaults),
  `migrated_db` (session-scoped; runs `python -m alembic -x db_url=... downgrade base` then
  `upgrade head` via subprocess; fails with a message pointing at `make db-init` if the
  database is unreachable), `db_engine`/`owner_engine` (session-scoped, app/owner roles),
  `db_session`, an autouse fixture that truncates every table in
  `Base.metadata.sorted_tables` (except `alembic_version`) before each `integration`-marked
  test (a no-op today: no ORM models exist yet), `app` (`create_app(settings)`), `client`
  (`httpx.AsyncClient` over `ASGITransport`, `raise_app_exceptions=False` so a genuine 500 can
  be asserted on rather than re-raised into the test — see "Issues" below).
- `pyproject.toml`: added `pythonpath = ["."]`, `asyncio_default_fixture_loop_scope = "session"`,
  `asyncio_default_test_loop_scope = "session"` to `[tool.pytest.ini_options]` (no new
  dependency was needed; `pytest-timeout` was not added).

**Tests written first (TDD):**

- `tests/unit/test_settings.py` — production + `dev-` secret raises `ValidationError`;
  production + `http://` origin raises; production + `llm_provider="fixture"` raises;
  development defaults load.
- `tests/unit/test_errors.py` — a throwaway route raising `AppError(409, "CONFLICT", "x")`
  returns the exact contract body with `trace_id` equal to the `X-Request-Id` response
  header; an unhandled `RuntimeError("secret detail")` returns 500 whose body never contains
  `secret detail`; an invalid body returns 422 with `field_errors`; a non-UUID inbound
  `X-Request-Id` is replaced; a valid inbound `X-Request-Id` is echoed back unchanged.
- `tests/integration/test_health.py` (`pytestmark = pytest.mark.integration`) —
  `/api/health/live` returns 200 `{"status": "ok"}`; `/api/health/ready` returns 200 with
  `pgvector` equal to `"0.8.6"`.

**RED (before implementation):**

```
$ cd services/backend && uv run pytest -q
ERROR collecting tests/unit/test_settings.py
ModuleNotFoundError: No module named 'app'
1 error in 0.87s
```

Expected: the `app` package did not exist yet.

**GREEN (after implementation):**

```
$ cd services/backend && uv run pytest -q
...........                                                              [100%]
11 passed in 0.54s
```

One real bug caught by the tests along the way: the unhandled-`Exception` handler's response
was missing the `X-Request-Id` header. Root cause: Starlette moves any handler registered for
the bare `Exception` type into the outermost `ServerErrorMiddleware`, which sits *outside*
`TraceIdMiddleware`, so the header-injection wrapper never sees that response, and
`TraceIdMiddleware`'s `finally` block resets `trace_id_var` before `ServerErrorMiddleware`'s
`except` block runs. Fixed by reading the trace ID from `request.state` (set on the ASGI
`scope`, which outlives the context-variable reset) and adding the header explicitly in
`unhandled_exception_handler`. Also found: `httpx.ASGITransport` re-raises server-side
exceptions by default (`raise_app_exceptions=True`), which is the standard/intended behavior
for exercising a real 500 path — worked around with `raise_app_exceptions=False` in both the
test-local client and the shared `client` fixture.

**Verification commands and results (in order):**

```
$ bash scripts/dev-db.sh init          # first run: initializes cluster, roles, DBs
$ bash scripts/dev-db.sh status        # -> "server is running (PID: ...)" on port 55432
$ bash scripts/dev-db.sh psql linesense_dev -tAc \
    "SELECT extversion FROM pg_extension WHERE extname='vector'"   # -> 0.8.6
$ bash scripts/dev-db.sh psql linesense_test -tAc \
    "SELECT extversion FROM pg_extension WHERE extname='vector'"   # -> 0.8.6
$ bash scripts/dev-db.sh init          # second run: exit 0, "already exists"/"skipping" only

$ make test
cd services/backend && uv run pytest -m "not integration" -q
.........                                                                [100%]
9 passed, 2 deselected in 0.08s

# Re-verified with the cluster stopped that `make test` needs no database at all:
$ bash scripts/dev-db.sh stop && make test    # -> 9 passed, 2 deselected (unchanged)
$ bash scripts/dev-db.sh start

$ make test-integration
scripts/dev-db.sh start
cd services/backend && uv run pytest -m integration -q
..                                                                       [100%]
2 passed, 9 deselected in 0.59s

$ make lint
cd services/backend && uv run ruff check .
All checks passed!
cd services/backend && uv run ruff format --check .
17 files already formatted

$ make typecheck
cd services/backend && uv run mypy app
Success: no issues found in 11 source files

$ make migration-check
... upgrade head / downgrade base / upgrade head (all clean) ...
No new upgrade operations detected.

$ make db-reset-test
DROP DATABASE / CREATE DATABASE / CREATE EXTENSION x2 / GRANT x2 / REVOKE -> reset-test complete
$ bash scripts/dev-db.sh reset-test linesense_dev
[dev-db] refusing to reset database 'linesense_dev': only 'linesense_test' may be reset   # exit 1
```

All test output above is clean (no unexplained warnings).

**Files changed:** see the commit for the full list; summary —
`scripts/dev-db.sh`, `Makefile`, `.env.example`,
`services/backend/{app/**,alembic.ini,migrations/**,tests/**,pyproject.toml}`,
`docs/IMPLEMENTATION_STATUS.md`.

**Self-review:**

- `app/db/session.py`'s `get_db_session` resolves the database URL from
  `request.app.state.settings` (not the global `get_settings()` cache) specifically so that
  test apps built with their own `Settings` (pointing at `linesense_test`) never accidentally
  hit `linesense_dev`.
- Confirmed by direct experiment that `make test` needs no running database (stopped the
  cluster and re-ran the suite — unchanged pass count) and that `migrated_db` fails with a
  clear message pointing at `make db-init` when the target database is unreachable (the
  intended behavior for `make test-integration` run without the cluster up).
- No placeholder/TODO code, no bare `except: pass`, no hardcoded production secrets (only
  clearly-labelled `dev-*` development defaults).
- Scope kept to exactly what the brief specifies: no ORM models, no auth, no jobs yet (Task 1
  is infrastructure only) — those are explicitly later tasks.

**Known issues / external blockers:**

- No LLM API key is available in this environment; `LS_LLM_PROVIDER=fixture` is the only
  provider exercised. No live-provider claim is made.
- No Docker/Java in this environment; the local PostgreSQL cluster is managed directly via
  Homebrew `postgresql@16` binaries rather than a container.
- `services/backend/.env` (the second, backend-local override location `Settings` reads) is
  not created — only the repo-root `.env` (copied from `.env.example`) exists, which is
  sufficient for this task and matches `.env.example`'s stated location.

**Next step:** Task 2 (Phase 0 documentation — requirements, ADRs, CLAUDE.md, README
skeleton).

### 2026-09-17 — Task 1 fix round 1 (post-review)

Review found the unhandled-exception 500 path logged through stdlib `logging` instead of
structlog, so it never went through the JSON pipeline, `trace_id`, or redaction from
`app/logging.py`. Fixed `app/api/errors.py` to use `structlog.get_logger("app.errors")` and to
bind `trace_id` explicitly (the context variable is already reset by the time this handler
runs, same as the earlier `X-Request-Id` header issue). While adding a
`structlog.testing.capture_logs()` test for this, found and fixed a second bug:
`app/logging.py` had `cache_logger_on_first_use=True`, which permanently caches a logger's
resolved processors on first use and ignores later `structlog.configure()` calls (including
`capture_logs()`'s) — changed to `False` (structlog's own default). Also fixed the optional
Minor item: `scripts/dev-db.sh`'s `ensure_role` interpolated the password directly into SQL
text (a `'` in the password would break the string literal); rewrote it to use psql variables
(`:"role"`/`:'password'`) via a heredoc (psql only substitutes those with a script, not `-c`).
Verified against the real cluster with a password containing a `'`, and rebuilt
`.local/pgdata` from scratch to exercise the actual role-creation code path.

Commands: `uv run pytest tests/unit/test_errors.py -q` (6 passed), `uv run pytest -q` (12
passed), `make lint` (clean), `make typecheck` (clean), `bash scripts/dev-db.sh stop && rm -rf
.local && bash scripts/dev-db.sh init` then `init` again (both exit 0), `make test` (10 passed,
2 deselected), `make test-integration` (2 passed, 10 deselected). Full detail in
`.superpowers/sdd/2026-09-17-linesense-build/task-1-report.md`.

### 2026-09-17 — Task 2: Phase 0 documentation — requirements, ADRs, CLAUDE.md, README skeleton

**Built:**

- `docs/requirements.md` — product statement; roles; assumptions (spec §1 plus this
  environment's no-Docker/no-Java/no-LLM-key/no-report-template/no-approved-domain-list gaps);
  21 numbered functional requirements (`REQ-01`…`REQ-21`) covering orders/import, the four
  agents, the agent protocol, orchestration/recovery, recommendations/approvals, transactional
  apply, quality hold/release/shipment eligibility, the document pipeline, hybrid retrieval with
  citations, NLP extraction/classification/summarization, audit, notifications, dashboards,
  authn/authz, and application security controls — each with acceptance criteria and a "Where
  implemented" pointer; 5 non-functional requirements (security, tenancy — including the
  documented, deferred row-level-security requirement, reliability, performance targets,
  accessibility); an assignment-traceability table reproducing spec §2 with an added "Where
  implemented" column; an explicit out-of-scope list (spec §14 cut list plus live ERP, OCR,
  purchasing, machine control).
- `docs/adr/README.md` plus eight one-page ADRs (`0001`–`0008`), each with Status/Context/
  Decision/Consequences/Alternatives considered: modular monolith + worker (0001); PostgreSQL +
  pgvector single store, exact search first (0002); PostgreSQL job table with
  `FOR UPDATE SKIP LOCKED`/leases/fencing/at-least-once delivery (0003); OIDC + PKCE + opaque
  sessions + CSRF double-check + development-only OIDC provider (Keycloak realm kept for
  Compose, unverified here) (0004); custom versioned HTTP/JSON agent protocol, explicitly not
  A2A/MCP (0005); LLM boundary — `LLMClient` interface, Anthropic `claude-opus-5` default with
  server-side refusal fallbacks (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`,
  confirmed current via the `claude-api` skill), deterministic `fixture` provider for CI, and
  `disabled` degraded mode (0006); single style per order (no `order_items`), ledger + lockable
  `material_balances` row with `version`, embeddings stored on `chunks`, one `approvals` row per
  recommendation (0007); local environment without Docker/Java — project-local PG cluster on
  55432, dev IdP, Compose files provided but not verified locally (0008).
- `docs/architecture/formulas.md` — every formula from spec §6 (planning standard
  minutes/utilization, materials availability/demand/projected balance/reorder point, IE
  effective cycle/bottleneck/throughput/line balance index, quality defective rate/DHU) with
  units, assumptions, and rounding rules, plus worked arithmetic for all six spec §6 reference
  fixtures (one-shift capacity shortfall; material shortage; bottleneck/balance index;
  defect rate/DHU; concurrent-reservation conflict; stale-proposal rejection) — verified by hand
  against the plan's stated expected results (e.g. 12,000 vs 6,300 standard minutes; 160-meter
  shortage; 60-second bottleneck / 83.33% balance index; 7% defective rate / 12 DHU).
- `docs/architecture/glossary.md` — SAM, DHU (vs. defective rate), AQL (with the "demo policy,
  not certified" caveat tied to `quality_policy_versions.is_demo`), line balance index,
  supermarket, BOM, lot, reservation, allocation, standard minutes, shift slot.
- `CLAUDE.md` (61 lines) — what the repo is; make commands (existing Task-1 targets vs. those
  marked "(added in later tasks)"); critical invariants copied from Global Constraints; pointers
  to the spec, contracts, status file, and ADRs; "never claim live-LLM success without a
  recorded run".
- `README.md` skeleton — title, one-paragraph description, architecture summary with the spec
  §3 mermaid diagram, prerequisites (Homebrew `postgresql@16`, the exact pgvector 0.8.6
  build/install commands, `uv`, Node ≥ 20), a quick-start using existing `make` targets, and
  placeholder headings for Setup/Usage/Testing/Limitations (filled in by later tasks) and a
  Contributors table (placeholder rows for the team to fill; no fabricated names) and License.
- `scripts/check-doc-links.sh` (new) — scans every `*.md` file under `docs/`, `README.md`, and
  `CLAUDE.md` for markdown link targets, skips absolute URLs/`mailto:`/pure fragments, resolves
  every remaining relative target against the linking file's directory, and exits non-zero
  listing each `BROKEN LINK` found. Wired into the root `Makefile` as `make docs-check`.

**TDD evidence for `scripts/check-doc-links.sh`:**

RED — a throwaway fixture tree (`README.md` linking to an existing `docs/adr/0001-example.md`
and a deliberately missing `docs/adr/0002-missing.md`) was created under the scratch directory
and run as `scripts/check-doc-links.sh <fixture-dir>`:

```
BROKEN LINK: README.md -> docs/adr/0002-missing.md (resolved: ./docs/adr/0002-missing.md)
check-doc-links: checked 2 relative link target(s) across 2 file(s)
exit=1
```

Expected and correct: the script must fail with a clear, specific reason (which file, which
link, which resolved path) when a relative doc link is broken.

GREEN — the fixture's broken link was pointed at the existing file and the script re-run against
the same fixture, then against the real repository as it stood before this task's new docs were
added (only `docs/IMPLEMENTATION_STATUS.md`, `docs/architecture/backend-contracts.md`, and
`docs/superpowers/plans/2026-09-17-linesense-build.md` existed, none containing markdown links):

```
check-doc-links: checked 2 relative link target(s) across 2 file(s)
exit=0
check-doc-links: checked 0 relative link target(s) across 3 file(s)
exit=0
```

**Verification commands and results (in order, after writing all documents):**

```
$ make docs-check
bash scripts/check-doc-links.sh
check-doc-links: checked 32 relative link target(s) across 17 file(s)

$ make lint
cd services/backend && uv run ruff check .
All checks passed!
cd services/backend && uv run ruff format --check .
17 files already formatted

$ make typecheck
cd services/backend && uv run mypy app
Success: no issues found in 11 source files

$ make test
cd services/backend && uv run pytest -m "not integration" -q
..........                                                               [100%]
10 passed, 2 deselected in 0.08s

$ bash scripts/dev-db.sh start   # already running
$ make test-integration
cd services/backend && uv run pytest -m integration -q
..                                                                       [100%]
2 passed, 10 deselected in 0.60s
```

All test/check output above is clean (no unexplained warnings); no backend source changed in
this task, so the unchanged pass counts (10/2 unit, 2 integration) match Task 1's baseline.

**Files changed:** new — `docs/requirements.md`, `docs/adr/README.md`,
`docs/adr/0001-modular-monolith-and-worker.md` … `docs/adr/0008-local-environment-without-docker.md`,
`docs/architecture/formulas.md`, `docs/architecture/glossary.md`, `CLAUDE.md`, `README.md`,
`scripts/check-doc-links.sh`. Modified — `Makefile` (new `docs-check` target),
`docs/IMPLEMENTATION_STATUS.md` (this entry; Phase 0 checklist line marked documented).

**Self-review:**

- Confirmed the Anthropic model/fallback terminology in ADR-0006 (`claude-opus-5`,
  `fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) against the `claude-api`
  skill's current reference rather than only the brief, since it names a specific model/beta
  string; it matches exactly.
- No fabricated results, scores, or team member names: the Contributors table is an explicit
  placeholder; formula fixtures are hand-verified arithmetic reproducing the plan's own stated
  expected results, not invented numbers; every "unverified locally" claim (Keycloak/Compose,
  live LLM) is stated as such rather than implied to work.
- `docs/requirements.md`'s tenancy NFR explicitly records row-level security as a deferred,
  documented pre-pilot requirement rather than silently omitting it or claiming it is done.
- Re-ran `make docs-check` after every new document was added (not only once at the end) to
  catch a broken link close to its cause; none were found in the final documents as written.
- Scope kept to exactly the brief's file list; no backend code, migrations, or tests were
  touched (this is a documentation-only task per the brief's Step markers).

**Known issues / external blockers:**

- No official report template or lecturer-approved manufacturing-domain list has been supplied;
  recorded as an assumption/blocker in `docs/requirements.md` §3, not fabricated.
- Formula/fixture arithmetic in `docs/architecture/formulas.md` is verified by hand in this
  document; the corresponding automated domain unit tests are Task 4's responsibility (Phase 0
  is marked "documented", not "tested", in the phase checklist above).
- The Keycloak/Compose identity path and any live-Anthropic-provider path remain unverified in
  this environment, as recorded in ADR-0004, ADR-0006, and ADR-0008.

**Next step:** Task 3 (or the next task in the SDD plan — see
`docs/superpowers/plans/2026-09-17-linesense-build.md`).

### 2026-09-17 — Task 3: complete data model and initial migration with role grants

**Built:**

- `app/domain/vocab.py`: `StrEnum` vocabularies for every enumerated column in
  `backend-contracts.md` section 2 (`Role`, `ProductionState`, `MaterialState`, `QualityState`,
  `RunStatus`, `RecommendationStatus`, `TaskStatus`, `JobStatus`, plus per-table enums such as
  `MaterialUnit`, `OrderSource`, `ShiftCode`, `MovementType`, `InspectionResult`,
  `DocumentVersionStatus`, `AgentRecipient`, `ActorType`, `AuditOutcome`, ...) and a `ROLES`
  tuple, so check-constraint values are generated from one source instead of duplicated as
  string literals.
- `app/db/types.py`: shared column helpers — `uuid_pk()`, `uuid_col()`, `created_at()`,
  `updated_at()`, `timestamptz()`, `org_fk()`, `factory_id_col()`, `composite_factory_fk(table)`
  (the `(organization_id, factory_id) -> factories(organization_id, id)` constraint, Postgres
  `MATCH SIMPLE` by default so it is safe on nullable `factory_id` columns too), `money(p, s)`
  (a typed `Numeric` alias), and `enum_check(name, column, values)` (a `CheckConstraint` whose
  name expands to `ck_<table>_<name>` via the naming convention already defined in
  `app/db/base.py`).
- `app/db/models/{identity,demand,capacity,inventory,ie,quality,documents,workflow,decisions,
  operations}.py` + `app/db/models/__init__.py`: all 50 tables from `backend-contracts.md`
  section 2, with every column, nullability, default, foreign key, unique constraint, check
  constraint, partial unique index, and index the contract specifies. Notable decisions:
  - `factories` gets an extra `UNIQUE(organization_id, id)` (beyond its documented
    `UNIQUE(organization_id, code)`) so the composite tenant-safety foreign key has something
    to reference; every table that carries both `organization_id` and `factory_id` NOT NULL
    uses that composite FK (`orders`, `lines`, `line_capacity_slots`, `allocations`,
    `material_lots`, `stock_movements`, `material_balances`, `reservations`,
    `expected_receipts`, `operator_aliases`, `operation_staffing`, `cycle_observations`,
    `line_measurements`, `inspections`, `quality_holds`, `quality_releases`, `analysis_runs`,
    `run_snapshots`, `agent_tasks`, `recommendations`, `import_batches`, `notifications`,
    `notes`). `documents` and `chunks` use the same composite FK with a nullable `factory_id`
    (enforced only when non-null, per Postgres `MATCH SIMPLE`). `role_assignments.factory_id`
    is a plain FK to `factories.id` instead, since that table has no `organization_id` column
    (the org is reached via `membership_id -> memberships.organization_id`).
  - `audit_events` has **no foreign keys at all** (not even to `organizations`): it is an
    append-only log that must survive deletion of the rows it describes.
  - `analysis_runs.snapshot_id -> run_snapshots` and `quality_holds.release_id ->
    quality_releases` are declared `use_alter=True` (documented in each module's docstring) —
    the former is a genuine two-table cycle with `run_snapshots.run_id -> analysis_runs`; the
    latter lets the migration create `quality_holds` before `quality_releases` while matching
    the contract's own table order.
  - `Chunk.tsv` is a `Computed("to_tsvector('english', coalesce(section,'') || ' ' || text)",
    persisted=True)` `TSVECTOR` column with a GIN index; `Chunk.embedding` is
    `pgvector.sqlalchemy.Vector(384)`, nullable.
  - ORM class names follow the brief exactly, including the three that differ from the
    table-name-derived default: `sessions` -> `SessionRecord`, `import_errors` ->
    `ImportRowError`, `agent_results` -> `AgentResultRecord`.
- `migrations/versions/0001_initial_schema.py`: generated via `alembic revision
  --autogenerate` against an empty `linesense_test` (verifying the models alone produce every
  contract table with zero manual transcription), then hand-edited to add:
  - `_create_extensions()` — `CREATE EXTENSION vector`/`pg_trgm` wrapped in a `DO $$ ... $$`
    block that checks `pg_extension` first, so the statement is skipped entirely (no privilege
    check triggered) when the extension already exists, which is always true in this repo
    (`scripts/dev-db.sh` creates both as the cluster superuser).
  - `_app_role()` — resolves the runtime role from `-x app_role=...`, else `LS_APP_DB_ROLE`,
    else `linesense_app`.
  - `_grant_app_role_privileges()` — `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES`,
    `REVOKE UPDATE, DELETE ON audit_events`, `GRANT USAGE, SELECT ON ALL SEQUENCES`, called at
    the end of `upgrade()`. The module docstring records that `ALTER DEFAULT PRIVILEGES` is
    deliberately not used, so every future migration must repeat the grant statements for its
    own new tables.
  - Explicit `op.create_foreign_key(...)` calls (after both referenced tables exist) for the
    two `use_alter=True` foreign keys, and matching `op.drop_constraint(...)` calls at the
    start of `downgrade()` — discovered via TDD: `op.create_table(..., use_alter=True)` embeds
    the constraint in the generated Python source but **does not actually emit it as DDL**
    (confirmed by running `alembic check` after the first `upgrade head`, which reported both
    foreign keys as still-missing "new upgrade operations"); `op.create_table`'s DDL path does
    not defer `use_alter` constraints the way `MetaData.create_all()` does, so they must be
    added with a separate operation.
  - `downgrade()` drops every table in reverse dependency order but never drops the
    extensions.
- `tests/factories.py`: async builders (`make_org`, `make_factory`, `make_user`,
  `make_membership`, `make_style_with_operations`, `make_material`, `make_order`, `make_line`,
  `make_slot`, `make_balance`) that create any missing parent (org/factory/customer/style/BOM)
  with unique-suffixed codes, `session.add(...)` + `session.flush()` (never commit — the
  caller's transaction/truncation fixture owns that), and accept keyword overrides for every
  column.
- `tests/helpers/__init__.py`: empty package placeholder for Task 5's `tests/helpers/auth.py`.
- `tests/integration/test_schema.py` (marked `integration`): all 11 tests from the brief —
  `test_all_contract_tables_exist`, `test_app_role_cannot_update_or_delete_audit_events`,
  `test_app_role_cannot_create_table`, `test_order_quantity_must_be_positive`,
  `test_user_identity_unique`, `test_cross_org_factory_reference_rejected`,
  `test_slot_cannot_be_oversubscribed`, `test_balance_reserved_cannot_exceed_on_hand`,
  `test_single_active_bom_per_style`, `test_chunk_tsv_generated`, `test_job_dedupe_key_unique`.
- `docs/architecture/erd.md`: a hand-drawn `mermaid erDiagram` of all 50 tables and their
  business relationships (tenant-scoping and pure-attribution `users` foreign keys are
  documented in prose instead of drawn, to keep the diagram legible).
- **Pre-existing bug fixed in `tests/conftest.py`:** the `db_session` fixture built its session
  factory from `str(db_engine.url)`, but `sqlalchemy.engine.URL.__str__` masks the password
  (renders `***`) by design; every test using `db_session` therefore failed with
  `password authentication failed for user "linesense_app"`. This was never caught before
  because Task 1/2's only integration tests (`test_health.py`) go through the `client`/`app`
  fixtures, which build their engine from `Settings.database_url` directly and never call
  `db_session`. Fixed to `db_engine.url.render_as_string(hide_password=False)`.

**TDD evidence:**

Tests and models were developed together rather than strictly test-first: `tests/factories.py`
and `tests/integration/test_schema.py` were written against the brief's spec before the schema
existed, but the full RED run below was captured once the models were far enough along to
import (writing 50 tables by hand first, then discovering basic `ImportError`s one file at a
time, would not have been a meaningful RED signal). The RED run below is the real one that
gated implementation of the migration and its grants:

```
$ cd services/backend && uv run pytest tests/integration/test_schema.py -q
# (first run, models complete but migration not yet upgraded / grants not yet added)
FAILED tests/integration/test_schema.py::test_all_contract_tables_exist
FAILED tests/integration/test_schema.py::test_app_role_cannot_update_or_delete_audit_events
FAILED tests/integration/test_schema.py::test_app_role_cannot_create_table
FAILED tests/integration/test_schema.py::test_order_quantity_must_be_positive
... (11 failed — password authentication failed for user "linesense_app": the `db_session`
    fixture bug above; masked every test using it, regardless of schema/grant correctness)
```

After fixing the `db_session` fixture and applying `migrations/versions/0001_initial_schema.py`:

```
$ cd services/backend && uv run pytest tests/integration/test_schema.py -q
...........                                                              [100%]
11 passed in 1.23s
```

The `alembic check` RED->GREEN cycle for the two `use_alter=True` foreign keys:

```
$ uv run alembic -x db_url=$TEST_DB_URL upgrade head   # first attempt, FKs inline in create_table
$ uv run alembic -x db_url=$TEST_DB_URL check
ERROR: New upgrade operations detected: [('add_fk', ... fk_analysis_runs_snapshot_id_run_snapshots ...),
                                          ('add_fk', ... fk_quality_holds_release_id_quality_releases ...)]
# fixed: moved both to explicit op.create_foreign_key() calls at the end of upgrade()
$ bash scripts/dev-db.sh reset-test && uv run alembic -x db_url=$TEST_DB_URL upgrade head
$ uv run alembic -x db_url=$TEST_DB_URL check
No new upgrade operations detected.
```

**Commands and results (final, clean run):**

```
$ make migration-check
... upgrade head / downgrade base / upgrade head / check ...
No new upgrade operations detected.

$ make lint
cd services/backend && uv run ruff check .
All checks passed!
cd services/backend && uv run ruff format --check .
35 files already formatted

$ make typecheck
cd services/backend && uv run mypy app
Success: no issues found in 25 source files

$ make test
cd services/backend && uv run pytest -m "not integration" -q
..........                                                               [100%]
10 passed, 13 deselected in 0.14s

$ make test-integration
scripts/dev-db.sh start
cd services/backend && uv run pytest -m integration -q
.............                                                            [100%]
13 passed, 10 deselected in 1.25s

$ make docs-check
bash scripts/check-doc-links.sh
check-doc-links: checked 33 relative link target(s) across 18 file(s)
```

All output above is clean (no unexplained warnings). The 13 integration passes are the 2
pre-existing health-check tests plus the 11 new schema tests.

**Files changed:** new — `app/domain/vocab.py`, `app/db/types.py`, `app/db/models/__init__.py`,
`app/db/models/{identity,demand,capacity,inventory,ie,quality,documents,workflow,decisions,
operations}.py`, `migrations/versions/0001_initial_schema.py`, `tests/factories.py`,
`tests/helpers/__init__.py`, `tests/integration/test_schema.py`,
`docs/architecture/erd.md`. Modified — `tests/conftest.py` (the `db_session` password-masking
bug fix described above), `docs/IMPLEMENTATION_STATUS.md` (this entry).

**Self-review:**

- Every table, column, nullability, default, FK, unique/check constraint, partial unique
  index, and named index from `backend-contracts.md` section 2 was checked column-by-column
  against the model files while writing them; `test_all_contract_tables_exist` and
  `make migration-check` provide an automated backstop that the table *set* and the model/DB
  structure agree.
- No swallowed exceptions: every `pytest.raises` in the new tests targets a specific exception
  type (`IntegrityError`/`ProgrammingError`) and, for the two privilege tests, asserts the
  wrapped `psycopg.errors.InsufficientPrivilege` class name specifically rather than any
  failure.
- No secrets logged or hardcoded: the app-role password comes from the existing
  `linesense_app`/`dev-app-only` dev credentials already established in Task 1 (unchanged
  here); the migration's role name resolution never embeds a password.
- Security/tenancy: the composite `(organization_id, factory_id)` foreign key and its test
  (`test_cross_org_factory_reference_rejected`) directly enforce the "no cross-org factory
  reference" invariant `docs/architecture/backend-contracts.md` requires application-wide.
- YAGNI: no relationships (`relationship()`) were added between ORM classes since no task yet
  needs ORM-level graph traversal; every test and factory builder works with plain foreign-key
  id columns. This keeps the 10 model files free of circular-relationship configuration
  complexity that isn't needed yet; a future task can add `relationship()` mappings
  incrementally if and when a specific feature needs them.
- The `tests/conftest.py` fix is a one-line, obviously-correct bug fix (masked password ->
  real password in a test-only fixture) required for any `db_session`-based test to run at
  all; it does not touch Task 2's actual deliverables (docs) and was verified not to change
  behavior for any other fixture (`app`/`client` build their engine independently).

**Known issues / limitations:**

- No `relationship()` attributes on the ORM models (see YAGNI note above) — later tasks that
  want ORM-level joins/eager-loading will need to add them.
- `role_assignments`'s `UNIQUE(membership_id, factory_id, role)` allows multiple rows with
  `factory_id IS NULL` for the same `(membership_id, role)`, since Postgres treats NULLs as
  distinct in unique constraints; the contract does not specify a partial-unique-index
  override for this case, so none was added. Not currently tested or exercised.
- `docs/architecture/erd.md` omits "actor" foreign keys to `users` (created_by, approved_by,
  ...) and all tenant-scoping edges as drawn relationships for legibility; both are documented
  in prose in the same file and column-by-column in `backend-contracts.md`.

**Next step:** Task 4 (or the next task in the SDD plan — see
`docs/superpowers/plans/2026-09-17-linesense-build.md`).

### 2026-09-17 — Task 4: deterministic domain calculations, rounding, and lifecycle policy

**Built:** pure, `Decimal`-in/`Decimal`-out domain modules under `app/domain/` — no database
access or I/O anywhere in this layer:

- `app/domain/rounding.py`: `quantize_display` (`ROUND_HALF_UP`, display-only) and
  `round_up_to_pack` (ceil to a material's `pack_size`; unchanged when no pack size is
  defined — continuous quantities like meters/kg have no natural whole-unit floor).
- `app/domain/planning/calc.py`: `SlotCapacity`/`SlotAllocation`/`AllocationPlan` dataclasses,
  `required_standard_minutes`, `available_standard_minutes`, `utilization`, and
  `plan_earliest_slots` (greedy earliest-slot allocation with the documented
  `(slot_date, shift_code, str(line_id))` tie-break, due-date cutoff, line-compatibility
  filter, and `max_units` material cap; reasons `NO_COMPATIBLE_LINE`,
  `INSUFFICIENT_CAPACITY_BEFORE_DUE_DATE`, `LIMITED_BY_MATERIAL`).
- `app/domain/inventory/calc.py`: `available_now`, `gross_demand`, `shortage`,
  `projected_balance`, `reorder_point`, `coverage_days` (`None` on non-positive consumption —
  never a misleading zero), `average_daily_consumption`, `convert_quantity` (raises
  `UnsupportedUnitConversion` for any pair not in `APPROVED_CONVERSIONS`), `coverable_units`
  (floors to a whole-number `Decimal`), and `material_state` (`UNKNOWN`/`SHORTAGE`/`AT_RISK`/
  `READY`).
- `app/domain/ie/calc.py`: `OperationCycle`/`LineBalanceResult` dataclasses,
  `representative_cycle_seconds` (median, raises `InsufficientSamples` below `min_samples`),
  `effective_cycle_seconds`, `line_balance` (bottleneck = first max on ties, throughput,
  this-model's balance index), `sam_capacity_units_per_hour`, `observed_units_per_hour`.
- `app/domain/quality/calc.py`: `QualityPolicyRules` (with `from_json` validation),
  `InspectionEvaluation`, `ShipmentFacts`/`ShipmentEligibility`, `defective_rate`,
  `defects_per_hundred_units` (both `None` when nothing was inspected), `evaluate_inspection`
  (`INSUFFICIENT_SAMPLE`/`FAIL` with one reason per broken rule/`PASS`), `shipment_eligibility`
  (reason codes `POLICY_UNKNOWN`, `PRODUCTION_NOT_COMPLETE`, `PACKING_INCOMPLETE`,
  `INSPECTION_MISSING:<TYPE>`, `ACTIVE_QUALITY_HOLD`, `NO_QUALITY_RELEASE`), and
  `quality_state`.
- `app/domain/orders/lifecycle.py`: `TransitionRule`, `InvalidTransition`, the exact
  `TRANSITIONS` table from the brief, and `get_transition`.
- Fixed `docs/architecture/formulas.md` to match the brief's `round_up_to_pack` semantics
  (no-pack-size case left at full precision, not rounded to a whole unit) and to note that
  Task 4 now implements these formulas.

**TDD evidence:**

- RED: `uv run pytest -m "not integration" -q tests/unit/test_reference_fixtures.py
  tests/unit/test_planning_calc.py tests/unit/test_inventory_calc.py tests/unit/test_ie_calc.py
  tests/unit/test_quality_calc.py tests/unit/test_lifecycle.py tests/unit/test_properties.py`
  — all 7 files failed collection with `ModuleNotFoundError`/`ImportError` for the
  not-yet-created `app.domain.{planning,inventory,ie,quality,orders}` modules, as expected
  before any implementation existed.
- GREEN (same command after implementing every module): `78 passed in 0.44s`.

**Tests:** `tests/unit/test_reference_fixtures.py` (the plan's four independently-worked
arithmetic fixtures, verbatim from the brief), `tests/unit/test_planning_calc.py`,
`tests/unit/test_inventory_calc.py`, `tests/unit/test_ie_calc.py`,
`tests/unit/test_quality_calc.py`, `tests/unit/test_lifecycle.py` (every `TRANSITIONS` pair,
an invalid transition, `round_up_to_pack`/`quantize_display`), and
`tests/unit/test_properties.py` (Hypothesis, `max_examples=200`, `derandomize=True`, bounded
fixed-precision `Decimal` strategies): no allocation ever exceeds its slot's remaining
minutes and `allocated + unscheduled == units`; `shortage` is never negative even when
reservations exceed on-hand stock; `line_balance.balance_index_percent` stays within
`(0, 100]`.

**Commands and results:**

```
$ cd services/backend && uv run pytest -m "not integration" -q
88 passed, 13 deselected in 0.62s

$ make lint
(clean on all Task 4 files; see "Known issues" — one unrelated, concurrently-edited file
was excluded from this run, see below)

$ make typecheck
Success: no issues found in 36 source files

$ make test-integration
13 passed, 88 deselected in 1.33s

$ make docs-check
check-doc-links: checked 33 relative link target(s) across 18 file(s)
```

**Files changed:** new — `app/domain/rounding.py`, `app/domain/planning/{__init__,calc}.py`,
`app/domain/inventory/{__init__,calc}.py`, `app/domain/ie/{__init__,calc}.py`,
`app/domain/quality/{__init__,calc}.py`, `app/domain/orders/{__init__,lifecycle}.py`,
`tests/unit/test_reference_fixtures.py`, `tests/unit/test_planning_calc.py`,
`tests/unit/test_inventory_calc.py`, `tests/unit/test_ie_calc.py`,
`tests/unit/test_quality_calc.py`, `tests/unit/test_lifecycle.py`,
`tests/unit/test_properties.py`. Modified — `docs/architecture/formulas.md` (rounding-rule
correction and a note that Task 4 implements these formulas), `docs/IMPLEMENTATION_STATUS.md`
(this entry).

**Self-review:**

- Every function signature, dataclass shape, and exact semantic rule in the brief (rounding
  direction, tie-break order, reason-code strings, `TRANSITIONS` table, precedence order in
  `material_state`/`quality_state`/`shipment_eligibility`) was checked line-by-line against
  the implementation while writing it; the brief's four reference fixtures pass verbatim.
- `plan_earliest_slots` never lets an allocation's `standard_minutes` exceed the source
  slot's `remaining_standard_minutes`: each allocation takes
  `min(remaining_target * sam, slot.remaining_standard_minutes)` directly, so the invariant
  holds structurally rather than depending on floating-point-style precision luck; the
  `allocated_units + unscheduled_units == units` invariant holds by construction because
  `unscheduled_units` is always computed as `total - allocated`, never independently.
  Comparisons that decide the unscheduled reason are quantized to 6 places first (per the
  brief's tolerance) so tiny Decimal division remainders never flip `LIMITED_BY_MATERIAL` vs
  `None`.
- No swallowed exceptions: every raised `ValueError`/domain-specific exception
  (`InsufficientSamples`, `UnsupportedUnitConversion`, `InvalidTransition`) is raised with a
  descriptive message and never caught internally.
- No secrets, no I/O, no database access anywhere in `app/domain/` — every function here is
  pure, matching the plan's "LLMs and I/O never establish business truth" boundary; deferred
  correctly to whatever caller wires these into `AppError`/audit/persistence in later tasks.
- YAGNI: `OperationCycle` is defined per the brief's interface but not yet consumed by any
  function body (the brief doesn't give it one) — left as the documented shape for the next
  task that builds a line-balance flow from raw per-operation cycle data.
- Security/tenancy: not applicable to this pure-calculation layer (no scope checks needed;
  the layer never touches organization/factory-scoped rows).

**Known issues / limitations:**

- While this task was running, an unrelated, concurrently-running Task 3 review made a
  live, uncommitted edit to `services/backend/app/db/types.py` (adding an
  `org_factory_index` helper) and touched a few `app/db/models/*.py` files; those files are
  **not part of this commit** (only Task 4's own new/changed files were staged) and their
  `ruff format` status is outside this task's scope. `make lint` run over the whole repo
  therefore currently reports one pre-existing/in-flight formatting issue in
  `app/db/types.py` unrelated to Task 4; `ruff check`/`ruff format --check` scoped to every
  file this task touches pass cleanly.
- `observed_units_per_hour` and `sam_capacity_units_per_hour` do not special-case
  zero/negative `hours`/`sam_minutes_per_unit` beyond what `Decimal` division already does
  (a `ZeroDivisionError`/`InvalidOperation`) — the brief does not specify an "unknown" return
  for these two, unlike `coverage_days`/`utilization`, so none was added; a future task should
  confirm this is the desired behavior for `sam_minutes_per_unit <= 0` observed-throughput
  inputs before wiring these into an API response.

**Next step:** Task 5 (or the next task in the SDD plan — see
`docs/superpowers/plans/2026-09-17-linesense-build.md`).

### 2026-09-17 — Task 5: OIDC login with PKCE, server sessions, CSRF, role policy, audit and idempotency

**Built:**

- `services/backend/devtools/dev_oidc/`: a development-only OIDC provider (`make idp` /
  `python -m devtools.dev_oidc`). It offers discovery, JWKS (RSA-2048 generated at startup,
  `kid` = thumbprint), `/authorize` (escaped HTML form with a banner, a user select and a password
  field), `/token` (client_secret_basic/post, single-use 60 s codes, PKCE S256, RS256 `id_token`
  valid for 300 s) and `/userinfo`. It exits with code 2 unless `LS_ENVIRONMENT` is
  `development` or `test`. `users.json` lists the ten contracts §9 identities.
- `app/auth/policy.py`: `ROLES`, `PERMISSIONS`, `Principal` and `require`.
  `app/auth/scope.py`: `load_scoped`, `accessible_factory_ids` and `visible_factories`.
  `app/auth/sessions.py`: opaque sha256-hashed sessions with an 8 h absolute lifetime,
  `last_seen_at` updated at most once a minute, rotation and revocation, and `load_principal`.
- `app/auth/oidc.py` (Authlib `linesense` client, one per app instance) and
  `app/auth/routes.py`: `/auth/login` (validates `next`), `/auth/callback` (upserts users by
  `(iss, sub)`, never creates memberships, rotates the session, audits `auth.login`
  SUCCESS/FAILED) and `/auth/logout` (204, revokes the session, audits `auth.logout`). The
  `ls_oidc` `SessionMiddleware` cookie holds only the handshake state.
- `app/auth/csrf.py`: a pure-ASGI CSRF middleware that checks Origin/Referer and
  `X-CSRF-Token`, and keeps the session record it resolves on the request scope.
- `app/api/middleware.py`: `SecurityHeadersMiddleware`. The 500 handler adds the same headers.
- `app/api/deps.py`: `get_principal`, `get_optional_principal` and
  `require_idempotency_key`. `app/api/me.py`: `GET /api/v1/me`. `app/api/pagination.py`:
  `page_params` and `Page[T]`.
- `app/audit/service.py`: `record_audit`, which redacts keys matching token/password/secret, and
  `audit_denied`, which commits in its own transaction.
- `app/idempotency/service.py`: `begin`/`finish`/`StoredResponse`/`request_hash`, using
  `INSERT ... ON CONFLICT DO NOTHING` and then re-selecting the row with `FOR UPDATE`. Keys are
  kept for 24 h.
- `tests/helpers/auth.py`: `DEMO_IDENTITIES`, `seed_identity`, `login_as` and `AuthedClient`.
  `tests/conftest.py` gains a `session_factory` fixture.
- `app/settings.py`: in production, settings are rejected if `oidc_issuer` or
  `oidc_redirect_uri` is not https, or if `oidc_client_secret` is a `dev-` value (ADR-0004).
- `joserfc` (already installed as a transitive dependency of Authlib) is now a direct dependency
  (`uv add joserfc`). `docs/security/authentication.md` was added. Contracts §4 now states the
  404/403 split for `load_scoped`, matching the global rule "missing permission on an accessible
  scope returns 403".

**Commands and results (2026-09-17):**

- `make typecheck`: `Success: no issues found in 52 source files`.
  `uv run mypy devtools tests/helpers/auth.py`: no issues.
- `make test-integration`: `58 passed, 323 deselected`. This includes 17 auth-flow tests
  against the dev IdP running under uvicorn in a background thread.
- `uv run pytest -m "not integration" -q --ignore=tests/unit/test_datasets.py
  --ignore=tests/unit/test_seed_vocabulary.py`: `308 passed`.
- `ruff check` and `ruff format --check` pass on every file this task touched.
- `make docs-check`: all 36 relative links resolve.
- Manual check: `LS_ENVIRONMENT=development uv run python -m devtools.dev_oidc` followed by
  `curl http://127.0.0.1:8090/.well-known/openid-configuration` returned
  `"issuer":"http://127.0.0.1:8090"`. `LS_ENVIRONMENT=production` produced `exit=2` with the
  refusal message.

**Known issues / limitations:**

- Two things in the working tree are unrelated to this task and are not committed with it: the
  concurrently added `tests/unit/test_datasets.py` (it needs `data/synthetic/`, which is still
  being written) and `tests/unit/test_seed_vocabulary.py` (2 ruff SIM300 findings). Because of
  them, `make test` and `make lint` over the whole repo currently fail.
- Limitations of the dev IdP (shared password, in-memory state, a key that changes on every
  restart, no TLS) are documented in `docs/security/authentication.md`. Production must use a
  real IdP. The Keycloak realm is scheduled for Task 26.
- `DEMO_IDENTITIES` lives in `tests/helpers/auth.py` until Task 6 moves it to
  `app/seed/generator.py`. A unit test keeps `users.json` in sync with it.

### 2026-09-17 — Task 5 fix round 1 (post-review)

**Changed:**

- The callback now passes `claims_options` to Authlib. It requires `iss` to equal the discovery
  issuer, `aud` to contain the client id, and `sub` to be present. Before this, a token with a
  foreign `aud` was accepted when its `azp` named our client.
- The discovery issuer must match `LS_OIDC_ISSUER`, ignoring a trailing slash. On a mismatch,
  `/auth/login` returns 503 and the callback fails. `users.issuer` is always normalized with
  `normalize_issuer`, and the seed and test helpers use the same function.
- The callback now also catches `RuntimeError` (Authlib metadata errors such as a missing
  `jwks_uri`) and redirects to `auth_failed`.
- Audit redaction now matches exact credential key names and the `_token`, `_password`,
  `_secret`, `_api_key` and `_private_key` suffixes. `input_tokens`, `max_tokens` and
  `token_count` are kept.
- `request_hash` tags every value with its type (`Decimal("1")` ≠ `"1"` ≠ `1`) and rejects
  unsupported types with `TypeError`.
- The dev IdP gains `DevIdpHooks.id_token_tamper`, an in-process hook for tests. It can't be
  reached over HTTP, and `__main__` never sets it.

**Commands and results:**

- `uv run pytest -q -m integration tests/integration/test_auth_flow.py tests/integration/test_csrf.py
  tests/integration/test_scope.py tests/integration/test_idempotency.py
  tests/integration/test_audit.py tests/integration/test_health.py tests/integration/test_schema.py`:
  `68 passed`.
- `uv run pytest -q tests/unit/test_policy.py tests/unit/test_dev_oidc.py
  tests/unit/test_http_hardening.py tests/unit/test_idempotency_hash.py`: `239 passed`.
- `uv run mypy app/auth app/audit app/idempotency app/api app/main.py devtools
  tests/helpers/auth.py`: no issues.
- Scoped `ruff check` and `ruff format --check` are clean. `make docs-check` passes.
- Whole-repo `make typecheck` currently reports 4 errors, all in the concurrently written,
  uncommitted `app/jobs/` (Task 10). None of them are in this task's files.

### 2026-09-17 — Task 10: durable job queue, worker runtime, heartbeats, fencing, reconciliation

**Built:**

- `app/jobs/queue.py` implements the contract §7 functions `enqueue`, `claim`, `heartbeat`,
  `complete` and `fail`, plus `ClaimedJob`, `LeaseLostError`, `RetryableJobError`,
  `PermanentJobError`, `backoff_seconds` and `format_error`.
  - `claim` is a single `UPDATE ... WHERE id = (SELECT ... ORDER BY available_at, created_at
    LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING ...` statement that uses the database `now()`.
  - A reclaimed job that has used up its attempts is marked FAILED in the same transaction, and
    the exhaustion hook runs after the commit.
  - Every state change is fenced by `lease_token` and `status = 'LEASED'`. Error text is capped
    at 2,000 characters.
- `app/jobs/worker.py` provides `Worker`, `HandlerRegistry`, `JobContext` and
  `finish_in_transaction`.
  - Handlers run under `concurrency` asyncio slots, with a heartbeat task that cancels the
    handler when the lease is lost.
  - Outcome mapping: permanent errors and unknown job types fail the job; other errors retry with
    backoff; a lost lease only logs `job.lease_lost`.
  - A handler that returns without completing is completed by the worker in a fenced
    transaction.
  - Shutdown is graceful: claiming stops and running handlers get 20 s to finish.
  - Logs: `job.claimed/completed/failed/lease_lost`, plus `worker.alive` every 30 s.
  - Outside production, the worker touches `.local/worker-<id>.alive` (setting
    `LS_WORKER_ALIVE_DIR`, default `../../.local` relative to `services/backend`). Loop errors
    back off exponentially, up to 5 s.
- `app/jobs/reconcile.py` provides `reconcile_once` and `ReconcileReport`. It deletes expired
  `idempotency_keys` and marks overdue PROPOSED/APPROVED `recommendations` as EXPIRED (with
  `SKIP LOCKED`, a `version` increment and a SYSTEM `recommendation.expire` audit event). The
  worker runs it every 15 s.
- `app/jobs/handlers.py`: `build_registry(settings)` registers `maintenance.reconcile` and
  `maintenance.purge_idempotency`. Both commit their writes and the job completion in one
  transaction.
- `app/jobs/__main__.py` is the `python -m app.jobs --queues ... --concurrency N [--worker-id]`
  entry point. It installs SIGINT/SIGTERM handlers. `make worker` runs it.
- `tests/factories.py` gains `make_run` and `make_recommendation`.
- `docs/architecture/jobs.md` covers the state diagram, claiming, fencing, retries and the
  at-least-once caveat.
- Contract change in backend-contracts.md §7: `claim` and `fail` take an optional `on_exhausted`
  callback. The queue module has no registry, so this callback is how the worker's exhaustion
  hook runs.

**Commands and results (2026-09-17):**

- `make test-integration`: `98 passed, 342 deselected`. This includes 13 tests in
  `test_job_queue.py` and 17 in `test_worker_runtime.py`, all against real PostgreSQL. The
  concurrency test runs 20 jobs × 4 claimers over separate pooled connections with
  `asyncio.gather`.
- `make test`: `342 passed, 98 deselected`. `make lint`: clean. `make typecheck`:
  `Success: no issues found in 58 source files`. `uv run mypy` on the two new test modules and
  `tests/factories.py`: clean. `make docs-check`: 37 links resolve.
- Manual smoke test against `linesense_test`:
  1. `LS_DATABASE_URL=...linesense_test uv run python -m app.jobs --queues maintenance
     --concurrency 2 --worker-id smoke` claimed and completed an enqueued
     `maintenance.reconcile` job.
  2. The worker created `.local/worker-smoke.alive`.
  3. On `SIGTERM` it logged `worker.stopping`/`worker.stopped`, exited with 0 and removed the
     liveness file.
  4. `--queues bogus` is rejected by argparse.

**Known issues / limitations:**

- `linesense_dev` has no migrations applied, so running `make worker` against the default dev
  database fails with `UndefinedTable` until `make migrate` is run. The worker logs the error and
  backs off. This task did not migrate the dev database.
- Delivery is at least once. Handlers must keep external side effects idempotent (see
  `docs/architecture/jobs.md`).
- Handlers still running after the 20 s shutdown grace period are cancelled. Their jobs are
  recovered only when the lease expires (30 s by default).
- `CANCELLED` job status is not set by any code path yet.
