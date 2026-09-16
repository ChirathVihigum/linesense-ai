#!/usr/bin/env bash
# Manage a project-local PostgreSQL 16 + pgvector cluster for LineSense AI.
#
# This script NEVER touches any PostgreSQL cluster other than the project-local
# one it creates under .local/pgdata. It does not start/stop/modify the
# Homebrew default cluster or use `brew services`.
#
# Subcommands: init | start | stop | status | psql | reset-test
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve PG_BIN: Homebrew postgresql@16 first, fall back to whatever
# pg_config reports (e.g. a non-Homebrew install with pgvector compiled in).
if [ -z "${PG_BIN:-}" ]; then
  if BREW_PREFIX="$(brew --prefix postgresql@16 2>/dev/null)" && [ -n "$BREW_PREFIX" ]; then
    PG_BIN="$BREW_PREFIX/bin"
  else
    PG_BIN="$(pg_config --bindir)"
  fi
fi

DATA_DIR="$REPO_ROOT/.local/pgdata"
SOCKET_DIR="$REPO_ROOT/.local/pgrun"
LOG_FILE="$REPO_ROOT/.local/pg.log"
PORT="${LS_DB_PORT:-55432}"
HOST="127.0.0.1"

SUPERUSER="linesense_super"
SUPERUSER_PASSWORD="${LS_DB_SUPERUSER_PASSWORD:-dev-super-only}"
OWNER_ROLE="linesense_owner"
OWNER_PASSWORD="${LS_DB_OWNER_PASSWORD:-dev-owner-only}"
APP_ROLE="linesense_app"
APP_PASSWORD="${LS_DB_APP_PASSWORD:-dev-app-only}"

DEV_DB="linesense_dev"
TEST_DB="linesense_test"

log() {
  echo "[dev-db] $*" >&2
}

pg_ctl_bin() { echo "$PG_BIN/pg_ctl"; }

is_running() {
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" status >/dev/null 2>&1
}

start_cluster() {
  if is_running; then
    log "cluster already running on port $PORT"
    return 0
  fi
  mkdir -p "$SOCKET_DIR"
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" -l "$LOG_FILE" -w \
    -o "-p $PORT -c listen_addresses=$HOST -c unix_socket_directories=$SOCKET_DIR" \
    start
  log "cluster started on port $PORT"
}

stop_cluster() {
  if ! is_running; then
    log "cluster is not running"
    return 0
  fi
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" -m fast -w stop
  log "cluster stopped"
}

status_cluster() {
  "$PG_BIN/pg_ctl" -D "$DATA_DIR" status
}

psql_super() {
  local db="$1"
  shift
  PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$SUPERUSER" -d "$db" -v ON_ERROR_STOP=1 "$@"
}

role_exists() {
  local role="$1"
  local out
  out="$(psql_super postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$role'")"
  [ "$out" = "1" ]
}

ensure_role() {
  local role="$1"
  local password="$2"
  if role_exists "$role"; then
    log "role $role already exists"
  else
    psql_super postgres -c "CREATE ROLE $role LOGIN PASSWORD '$password'"
    log "created role $role"
  fi
}

database_exists() {
  local db="$1"
  local out
  out="$(psql_super postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'")"
  [ "$out" = "1" ]
}

ensure_database() {
  local db="$1"
  if database_exists "$db"; then
    log "database $db already exists"
  else
    psql_super postgres -c "CREATE DATABASE $db OWNER $OWNER_ROLE"
    log "created database $db"
  fi
}

configure_database() {
  local db="$1"
  psql_super "$db" -c "CREATE EXTENSION IF NOT EXISTS vector;"
  psql_super "$db" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
  psql_super "$db" -c "GRANT CONNECT ON DATABASE $db TO $APP_ROLE;"
  psql_super "$db" -c "GRANT USAGE ON SCHEMA public TO $APP_ROLE;"
  psql_super "$db" -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
  log "configured database $db (vector, pg_trgm, grants)"
}

cmd_init() {
  mkdir -p "$REPO_ROOT/.local"
  if [ -f "$DATA_DIR/PG_VERSION" ]; then
    log "data directory already initialized: $DATA_DIR"
  else
    local pwfile
    pwfile="$(mktemp)"
    printf '%s' "$SUPERUSER_PASSWORD" >"$pwfile"
    chmod 600 "$pwfile"
    "$PG_BIN/initdb" -D "$DATA_DIR" -E UTF8 \
      --username="$SUPERUSER" \
      --auth=scram-sha-256 \
      --pwfile="$pwfile"
    rm -f "$pwfile"
    log "initialized data directory: $DATA_DIR"
  fi

  start_cluster

  ensure_role "$OWNER_ROLE" "$OWNER_PASSWORD"
  ensure_role "$APP_ROLE" "$APP_PASSWORD"

  ensure_database "$DEV_DB"
  ensure_database "$TEST_DB"

  configure_database "$DEV_DB"
  configure_database "$TEST_DB"

  log "init complete"
}

cmd_reset_test() {
  local target="${1:-$TEST_DB}"
  if [ "$target" != "$TEST_DB" ]; then
    log "refusing to reset database '$target': only '$TEST_DB' may be reset"
    exit 1
  fi
  if ! is_running; then
    log "cluster is not running; start it first with: $0 start"
    exit 1
  fi
  psql_super postgres -c "DROP DATABASE IF EXISTS $TEST_DB;"
  ensure_database "$TEST_DB"
  configure_database "$TEST_DB"
  log "reset-test complete"
}

cmd_psql() {
  local db="${1:-postgres}"
  shift || true
  exec env PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$SUPERUSER" -d "$db" "$@"
}

usage() {
  cat >&2 <<EOF
Usage: $0 <init|start|stop|status|psql [db] [psql-args...]|reset-test [db]>
EOF
}

main() {
  local subcommand="${1:-}"
  case "$subcommand" in
    init)
      cmd_init
      ;;
    start)
      start_cluster
      ;;
    stop)
      stop_cluster
      ;;
    status)
      status_cluster
      ;;
    psql)
      shift
      cmd_psql "$@"
      ;;
    reset-test)
      shift
      cmd_reset_test "$@"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
