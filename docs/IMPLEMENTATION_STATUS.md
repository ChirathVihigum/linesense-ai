# LineSense AI: implementation status

This file is a running, dated log of what has actually been built, tested, and verified,
kept alongside `LINESENSE_IMPLEMENTATION_PLAN.md` and `docs/architecture/backend-contracts.md`.
It is updated at the end of every task.

## Phase checklist

Phases from `LINESENSE_IMPLEMENTATION_PLAN.md` section 14 ("Implementation sequence and
milestones"):

- [ ] Phase 0: requirements and contracts — domain glossary, policies, threat model, data
      contracts, ADRs, official dependency check, synthetic fixtures
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
