#!/usr/bin/env bash
# Encrypted backup of a LineSense database + its document store
# (task-25-brief.md req. 8).
#
# Usage: scripts/backup.sh [database_name]
#   database_name defaults to linesense_dev.
#
# Requires LS_BACKUP_PASSPHRASE in the environment (refuses to run with an
# empty/unset passphrase). Writes .local/backups/<timestamp>.tar.enc:
# an AES-256-CBC (PBKDF2, salted) encrypted tar containing
#   - dump.pgdump       (`pg_dump -Fc` of the database)
#   - documents.tar      (tar of LS_DOCUMENT_STORAGE_DIR)
#   - manifest.json       ({"database": ..., "created_at": ..., "row_counts":
#                           {table: count, ...}})
#
# Never touches any cluster/database other than the one named on the command
# line (read-only there: only pg_dump is run against it).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/services/backend"

DATABASE_NAME="${1:-linesense_dev}"

if [ -z "${LS_BACKUP_PASSPHRASE:-}" ]; then
  echo "backup: LS_BACKUP_PASSPHRASE must be set (refusing to back up with no passphrase)" >&2
  exit 1
fi

if [ -z "${PG_BIN:-}" ]; then
  if BREW_PREFIX="$(brew --prefix postgresql@16 2>/dev/null)" && [ -n "$BREW_PREFIX" ]; then
    PG_BIN="$BREW_PREFIX/bin"
  else
    PG_BIN="$(pg_config --bindir)"
  fi
fi

HOST="127.0.0.1"
PORT="${LS_DB_PORT:-55432}"
OWNER_ROLE="${LS_DB_OWNER_ROLE:-linesense_owner}"
OWNER_PASSWORD="${LS_DB_OWNER_PASSWORD:-dev-owner-only}"

DOCUMENT_STORAGE_DIR="${LS_DOCUMENT_STORAGE_DIR:-$REPO_ROOT/.local/documents}"
if [[ "$DOCUMENT_STORAGE_DIR" != /* ]]; then
  DOCUMENT_STORAGE_DIR="$BACKEND_DIR/$DOCUMENT_STORAGE_DIR"
fi

BACKUP_DIR="$REPO_ROOT/.local/backups"
mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log() { echo "[backup] $*" >&2; }

log "dumping database '$DATABASE_NAME'"
PGPASSWORD="$OWNER_PASSWORD" "$PG_BIN/pg_dump" \
  -h "$HOST" -p "$PORT" -U "$OWNER_ROLE" -Fc -d "$DATABASE_NAME" \
  -f "$WORK_DIR/dump.pgdump"

log "recording table row counts"
TABLES="$(PGPASSWORD="$OWNER_PASSWORD" "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER_ROLE" -d "$DATABASE_NAME" \
  -tAc "select tablename from pg_tables where schemaname = 'public' order by 1")"

{
  printf '{\n  "database": "%s",\n  "created_at": "%s",\n  "row_counts": {\n' \
    "$DATABASE_NAME" "$TIMESTAMP"
  first=1
  while IFS= read -r table; do
    [ -z "$table" ] && continue
    count="$(PGPASSWORD="$OWNER_PASSWORD" "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER_ROLE" -d "$DATABASE_NAME" \
      -tAc "select count(*) from \"$table\"")"
    if [ "$first" -eq 1 ]; then first=0; else printf ',\n'; fi
    printf '    "%s": %s' "$table" "$count"
  done <<<"$TABLES"
  printf '\n  }\n}\n'
} >"$WORK_DIR/manifest.json"

log "archiving document storage ($DOCUMENT_STORAGE_DIR)"
if [ -d "$DOCUMENT_STORAGE_DIR" ]; then
  tar -cf "$WORK_DIR/documents.tar" -C "$(dirname "$DOCUMENT_STORAGE_DIR")" "$(basename "$DOCUMENT_STORAGE_DIR")"
else
  log "document storage dir does not exist; archiving an empty tar"
  tar -cf "$WORK_DIR/documents.tar" -T /dev/null
fi

OUT_FILE="$BACKUP_DIR/$TIMESTAMP.tar.enc"
log "encrypting into $OUT_FILE"
tar -cf - -C "$WORK_DIR" dump.pgdump documents.tar manifest.json |
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass "pass:$LS_BACKUP_PASSPHRASE" -out "$OUT_FILE"

log "done: $OUT_FILE"
echo "$OUT_FILE"
