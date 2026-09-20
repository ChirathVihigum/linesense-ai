#!/usr/bin/env bash
# Restore + verify a backup made by scripts/backup.sh (task-25-brief.md req. 8).
#
# Usage: scripts/restore.sh <backup-file.tar.enc>
#
# Always restores into the database `linesense_restore` (dropped and
# recreated by this script) and a separate document directory under
# .local/restore-documents/<timestamp> -- never into linesense_dev or any
# other target, so a mistaken run can never clobber real data.
#
# Verification (fails the script, non-zero exit, on any mismatch):
#   1. every table's restored row count equals the manifest recorded at
#      backup time;
#   2. every ACTIVE/SUPERSEDED document_versions.storage_key exists as a
#      file under the restored document directory;
#   3. every material_balances.on_hand_accepted equals the sum of its
#      material's ACCEPTED-lot stock_movements (backend-contracts.md
#      section 2's material_balances invariant).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RESTORE_DB="linesense_restore"

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/restore.sh <backup-file.tar.enc>" >&2
  exit 1
fi
BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
  echo "restore: no such file: $BACKUP_FILE" >&2
  exit 1
fi
if [ -z "${LS_BACKUP_PASSPHRASE:-}" ]; then
  echo "restore: LS_BACKUP_PASSPHRASE must be set" >&2
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
SUPERUSER="${LS_DB_SUPERUSER:-linesense_super}"
SUPERUSER_PASSWORD="${LS_DB_SUPERUSER_PASSWORD:-dev-super-only}"
OWNER_ROLE="${LS_DB_OWNER_ROLE:-linesense_owner}"
OWNER_PASSWORD="${LS_DB_OWNER_PASSWORD:-dev-owner-only}"

log() { echo "[restore] $*" >&2; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log "decrypting $BACKUP_FILE"
openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:$LS_BACKUP_PASSPHRASE" -in "$BACKUP_FILE" |
  tar -xf - -C "$WORK_DIR"
for required in dump.pgdump documents.tar manifest.json; do
  if [ ! -f "$WORK_DIR/$required" ]; then
    echo "restore: backup archive is missing $required" >&2
    exit 1
  fi
done

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESTORE_DOCS_DIR="$REPO_ROOT/.local/restore-documents/$TIMESTAMP"
mkdir -p "$RESTORE_DOCS_DIR"
log "extracting documents into $RESTORE_DOCS_DIR"
tar -xf "$WORK_DIR/documents.tar" -C "$RESTORE_DOCS_DIR"
# `backup.sh` archives the storage directory by its own basename (e.g.
# "documents/"); flatten that one level so RESTORE_DOCS_DIR itself is the
# storage root, matching what LS_DOCUMENT_STORAGE_DIR would point at.
if [ "$(find "$RESTORE_DOCS_DIR" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]; then
  inner="$(find "$RESTORE_DOCS_DIR" -mindepth 1 -maxdepth 1)"
  if [ -d "$inner" ]; then
    mv "$inner"/* "$RESTORE_DOCS_DIR"/ 2>/dev/null || true
    rmdir "$inner" 2>/dev/null || true
  fi
fi

log "recreating database $RESTORE_DB"
PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/dropdb" -h "$HOST" -p "$PORT" -U "$SUPERUSER" --if-exists "$RESTORE_DB"
PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/createdb" -h "$HOST" -p "$PORT" -U "$SUPERUSER" -O "$OWNER_ROLE" "$RESTORE_DB"
PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$SUPERUSER" -d "$RESTORE_DB" \
  -c "create extension if not exists vector; create extension if not exists pg_trgm;" >/dev/null

log "restoring dump into $RESTORE_DB"
# As the cluster superuser, not $OWNER_ROLE: `vector`/`pg_trgm` are created
# by the superuser (backend-contracts.md section 1), so the dump's `COMMENT
# ON EXTENSION ...` statements fail with "must be owner of extension" under
# any other role. `--no-owner` still makes every restored object's owner
# come out as whoever runs this (i.e. every table ends up owned by the
# superuser rather than $OWNER_ROLE) -- fine for a verification-only
# restore target that nothing else writes to.
PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/pg_restore" \
  -h "$HOST" -p "$PORT" -U "$SUPERUSER" -d "$RESTORE_DB" --no-owner \
  "$WORK_DIR/dump.pgdump"

psql_restore() {
  PGPASSWORD="$SUPERUSER_PASSWORD" "$PG_BIN/psql" -h "$HOST" -p "$PORT" -U "$SUPERUSER" -d "$RESTORE_DB" -tAc "$1"
}

log "verifying row counts against the backup manifest"
FAILED=0
TABLES="$(python3 -c "
import json
with open('$WORK_DIR/manifest.json') as f:
    manifest = json.load(f)
for table, count in manifest['row_counts'].items():
    print(f'{table}\t{count}')
")"
while IFS=$'\t' read -r table expected; do
  [ -z "$table" ] && continue
  actual="$(psql_restore "select count(*) from \"$table\"")"
  if [ "$actual" != "$expected" ]; then
    echo "restore: row count mismatch for $table: expected $expected, got $actual" >&2
    FAILED=1
  fi
done <<<"$TABLES"

log "verifying document_versions.storage_key files exist"
STORAGE_KEYS="$(psql_restore "select storage_key from document_versions where status in ('ACTIVE','SUPERSEDED')")"
while IFS= read -r key; do
  [ -z "$key" ] && continue
  if [ ! -f "$RESTORE_DOCS_DIR/$key" ]; then
    echo "restore: missing restored document file for storage_key $key" >&2
    FAILED=1
  fi
done <<<"$STORAGE_KEYS"

log "verifying material_balances against the movement ledger"
# material_lots (and therefore its movements) are scoped per factory, and a
# material_id can have a separate balance in each factory it is stocked in
# (unique(factory_id, material_id)) -- the join must match on both columns,
# not material_id alone, or one factory's movements would be summed into
# another factory's balance for the same material.
BALANCE_MISMATCHES="$(psql_restore "
  select mb.factory_id || ':' || mb.material_id
  from material_balances mb
  join material_lots ml
    on ml.material_id = mb.material_id
   and ml.factory_id = mb.factory_id
   and ml.status = 'ACCEPTED'
  left join stock_movements sm on sm.lot_id = ml.id
  group by mb.factory_id, mb.material_id, mb.on_hand_accepted
  having mb.on_hand_accepted <> coalesce(sum(sm.quantity), 0)
")"
if [ -n "$BALANCE_MISMATCHES" ]; then
  echo "restore: material_balances do not equal their ledger sums for material(s): $BALANCE_MISMATCHES" >&2
  FAILED=1
fi

if [ "$FAILED" -ne 0 ]; then
  echo "restore: verification FAILED" >&2
  exit 1
fi

log "verification passed. Restored database: $RESTORE_DB; documents: $RESTORE_DOCS_DIR"
