.PHONY: bootstrap db-init db-start db-stop db-reset-test migrate migration-check \
        lint format typecheck test test-integration test-all docs-check idp worker datasets-check seed \
        contracts contracts-check build web-install web-dev web-lint web-typecheck web-test web-build

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
	cd services/backend && uv run python -m app.seed

contracts:
	bash scripts/export-openapi.sh
	cd services/backend && uv run python ../../scripts/export-protocol-schemas.py
	cd $(WEB_DIR) && npm run generate:api

contracts-check:
	bash scripts/check-contracts.sh

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
