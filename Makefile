.PHONY: bootstrap db-init db-start db-stop db-reset-test migrate migration-check \
        lint format typecheck test test-integration test-all docs-check idp worker datasets-check seed \
        contracts contracts-check infra-check build web-install web-dev web-lint web-typecheck web-test web-build \
        eval security perf backup restore-check

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

WEB_DIR := apps/web

TEST_DB_URL ?= postgresql+psycopg://linesense_owner:dev-owner-only@127.0.0.1:55432/linesense_test

bootstrap:
	scripts/dev-db.sh init
	cp -n .env.example .env || true
	cd services/backend && uv sync

db-init:
	scripts/dev-db.sh init

db-start:
	scripts/dev-db.sh start

db-stop:
	scripts/dev-db.sh stop

db-reset-test:
	scripts/dev-db.sh reset-test

migrate:
	cd services/backend && uv run alembic upgrade head

migration-check:
	cd services/backend && uv run alembic -x db_url=$(TEST_DB_URL) upgrade head
	cd services/backend && uv run alembic -x db_url=$(TEST_DB_URL) downgrade base
	cd services/backend && uv run alembic -x db_url=$(TEST_DB_URL) upgrade head
	cd services/backend && uv run alembic -x db_url=$(TEST_DB_URL) check

lint:
	cd services/backend && uv run ruff check .
	cd services/backend && uv run ruff format --check .
	cd $(WEB_DIR) && npm run lint

format:
	cd services/backend && uv run ruff format .
	cd services/backend && uv run ruff check --fix .

typecheck:
	cd services/backend && uv run mypy app
	cd $(WEB_DIR) && npm run typecheck

test:
	cd services/backend && uv run pytest -m "not integration" -q
	cd $(WEB_DIR) && npm test

test-integration:
	scripts/dev-db.sh start
	cd services/backend && uv run pytest -m integration -q

test-all: test test-integration

docs-check:
	bash scripts/check-doc-links.sh

idp:
	cd services/backend && uv run python -m devtools.dev_oidc

datasets-check:
	cd services/backend && uv run python ../../scripts/validate_datasets.py

worker:
	cd services/backend && uv run python -m app.jobs

seed:
	cd services/backend && uv run python -m app.seed --with-documents

# Reproducible retrieval/NLP/calculation/agent/fairness/security evaluation
# (task-19-brief.md). Runs against the dedicated linesense_eval database
# (or LS_EVAL_DATABASE_URL/LS_EVAL_MIGRATION_DATABASE_URL if set), never
# linesense_dev/linesense_test. Uses the real fastembed embedder; see
# docs/evaluation/methodology.md for the lighter `--embedder hashing`
# smoke-test invocation used in local/heat-constrained development.
eval:
	cd services/backend && uv run python -m app.evaluation --embedder fastembed

# Security/resilience hardening (task-25-brief.md): secret scan, dependency
# audit, then every test module marked `security` (headers/rate-limits,
# the route-generated IDOR matrix, and the prompt-injection suite; some of
# these need scripts/dev-db.sh start, like test-integration).
security:
	bash scripts/secret-scan.sh
	bash scripts/dependency-audit.sh
	scripts/dev-db.sh start
	cd services/backend && uv run pytest -m security -q

# Performance smoke (task-25-brief.md req. 9): concurrent load against the
# seeded dev DB with a real uvicorn (2 workers, no reload). Heavier than any
# other `make` target here (it is the one this project's machine-heat policy
# says never to run unattended on a laptop) -- run only with the operator's
# explicit go-ahead, and see docs/evaluation/performance.md for the exact
# invocation and its recorded (or PENDING) results.
perf:
	cd services/backend && uv run python ../../scripts/perf_smoke.py

# Encrypted backup of $(BACKUP_DB) (default linesense_dev) + its document
# store (task-25-brief.md req. 8). Requires LS_BACKUP_PASSPHRASE in the
# environment; see scripts/backup.sh.
BACKUP_DB ?= linesense_dev
backup:
	bash scripts/backup.sh $(BACKUP_DB)

# Backup + restore + verification exercise, timed, on $(BACKUP_DB) (default
# linesense_dev). Restores into the dedicated `linesense_restore` database
# (dropped/recreated by scripts/restore.sh) and a separate document
# directory; never touches $(BACKUP_DB) itself beyond reading it. See
# docs/operations/backup-restore.md for recorded timings.
restore-check:
	@echo "restore-check: backing up $(BACKUP_DB)"; \
	time bash scripts/backup.sh $(BACKUP_DB); \
	latest=$$(ls -t .local/backups/*.tar.enc | head -1); \
	echo "restore-check: restoring $$latest"; \
	time bash scripts/restore.sh "$$latest"

contracts:
	bash scripts/export-openapi.sh
	cd services/backend && uv run python ../../scripts/export-protocol-schemas.py
	cd $(WEB_DIR) && npm run generate:api

contracts-check:
	bash scripts/check-contracts.sh

# Static validation of the deployment artefacts (Task 26): container
# images, Compose, Keycloak realm, and the CI workflow itself. No Docker
# commands are run; see scripts/validate-infra.py.
infra-check:
	cd services/backend && uv run python ../../scripts/validate-infra.py

# Backend import check (the app factory builds without a database) + web production build.
build:
	cd services/backend && uv run python -c "from app.main import create_app; create_app()"
	cd $(WEB_DIR) && npm run build

web-install:
	cd $(WEB_DIR) && npm ci

web-dev:
	cd $(WEB_DIR) && npm run dev

web-lint:
	cd $(WEB_DIR) && npm run lint

web-typecheck:
	cd $(WEB_DIR) && npm run typecheck

web-test:
	cd $(WEB_DIR) && npm test

web-build:
	cd $(WEB_DIR) && npm run build
