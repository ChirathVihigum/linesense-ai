#!/usr/bin/env bash
# Bootstrap the LineSense roles, application database, and extensions for
# the Compose-managed Postgres (service `db`, image pgvector/pgvector).
#
# Runs automatically and exactly once — the official Postgres entrypoint
# executes every file under /docker-entrypoint-initdb.d/ (as the
# POSTGRES_USER superuser, against POSTGRES_DB) the first time the data
# directory is initialized, and never again afterwards.
#
# Mirrors scripts/dev-db.sh's ensure_role / ensure_database /
# configure_database steps (see docs/architecture/backend-contracts.md
# section 1) for the Compose deployment instead of the local project
# cluster: two roles (linesense_owner: schema owner/migrations,
# linesense_app: DML-only runtime role) and one application database with
# the `vector` and `pg_trgm` extensions.
set -euo pipefail

: "${LINESENSE_OWNER_PASSWORD:?LINESENSE_OWNER_PASSWORD must be set (see infra/compose/.env.compose.example)}"
: "${LINESENSE_APP_PASSWORD:?LINESENSE_APP_PASSWORD must be set (see infra/compose/.env.compose.example)}"
: "${LINESENSE_DB_NAME:=linesense}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="$LINESENSE_OWNER_PASSWORD" \
  -v app_password="$LINESENSE_APP_PASSWORD" \
  -v db_name="$LINESENSE_DB_NAME" <<'SQL'
CREATE ROLE linesense_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE linesense_app LOGIN PASSWORD :'app_password';
CREATE DATABASE :"db_name" OWNER linesense_owner;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$LINESENSE_DB_NAME" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
GRANT CONNECT ON DATABASE "$LINESENSE_DB_NAME" TO linesense_app;
GRANT USAGE ON SCHEMA public TO linesense_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL

echo "[db-init] linesense_owner/linesense_app roles and database '$LINESENSE_DB_NAME' ready."
