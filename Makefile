.PHONY: bootstrap db-init db-start db-stop db-reset-test migrate migration-check \
        lint format typecheck test test-integration test-all docs-check

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

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

format:
	cd services/backend && uv run ruff format .
	cd services/backend && uv run ruff check --fix .

typecheck:
	cd services/backend && uv run mypy app

test:
	cd services/backend && uv run pytest -m "not integration" -q

test-integration:
	scripts/dev-db.sh start
	cd services/backend && uv run pytest -m integration -q

test-all: test test-integration

docs-check:
	bash scripts/check-doc-links.sh
