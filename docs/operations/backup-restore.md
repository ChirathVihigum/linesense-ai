# Backup and restore (task-25-brief.md req. 8)

`scripts/backup.sh [database_name]` (default `linesense_dev`) writes an encrypted, self-contained
archive to `.local/backups/<timestamp>.tar.enc`:

- `dump.pgdump` — `pg_dump -Fc` of the database (owner role, so schema + data + grants are all
  captured);
- `documents.tar` — a tar of `LS_DOCUMENT_STORAGE_DIR` (the on-disk document store);
- `manifest.json` — `{"database", "created_at", "row_counts": {table: count, ...}}`, taken at the
  same moment as the dump, for `restore.sh` to verify against later.

The three are tarred together and encrypted with `openssl enc -aes-256-cbc -pbkdf2 -salt`, keyed by
`LS_BACKUP_PASSPHRASE` (required; the script refuses to run without it — there is no default
passphrase to accidentally ship).

`scripts/restore.sh <backup-file.tar.enc>` decrypts the archive and restores it into a **dedicated**
database, `linesense_restore` (dropped and recreated by the script every run) and a **dedicated**
document directory, `.local/restore-documents/<timestamp>/` — never into `linesense_dev`, `_test`
or any other target, so a mistaken invocation can never overwrite real data. It then verifies:

1. every table's restored row count equals the manifest recorded at backup time;
2. every `document_versions` row with `status in ('ACTIVE', 'SUPERSEDED')` has its `storage_key` file
   present under the restored document directory;
3. every `material_balances.on_hand_accepted` equals the sum of its material's (same factory's)
   `ACCEPTED`-lot `stock_movements` — the ledger invariant from
   [backend-contracts.md §2](../architecture/backend-contracts.md).

Any mismatch fails the script (non-zero exit) with the specific table/material/document that did
not match.

`make backup` runs `scripts/backup.sh $(BACKUP_DB)` (default `linesense_dev`; override with
`make backup BACKUP_DB=...`). `make restore-check` runs backup, then restore-and-verify, on
`$(BACKUP_DB)`, timed.

## Recorded exercise (2026-09-20)

Run against the real project-local `linesense_dev` cluster (project-local PostgreSQL 16 +
pgvector, port 55432), which at the time held a full seeded demo dataset (`make seed`-shaped: 153
allocations, 61 BOM lines, 720 cycle observations, 26 customers, ... — see the manifest excerpt
below).

```
$ time LS_BACKUP_PASSPHRASE=*** bash scripts/backup.sh linesense_dev
[backup] dumping database 'linesense_dev'
[backup] recording table row counts
[backup] archiving document storage (.../.local/documents)
[backup] encrypting into .../.local/backups/20260920T072330Z.tar.enc
[backup] done: .../.local/backups/20260920T072330Z.tar.enc
real  0m0.87s

$ time LS_BACKUP_PASSPHRASE=*** bash scripts/restore.sh .local/backups/20260920T072330Z.tar.enc
[restore] decrypting .local/backups/20260920T072330Z.tar.enc
[restore] extracting documents into .../.local/restore-documents/20260920T071511Z
[restore] recreating database linesense_restore
[restore] restoring dump into linesense_restore
[restore] verifying row counts against the backup manifest
[restore] verifying document_versions.storage_key files exist
[restore] verifying material_balances against the movement ledger
[restore] verification passed. Restored database: linesense_restore; documents: .../.local/restore-documents/20260920T071511Z
real  0m1.07s
```

Backup: **0.87s**, 368,672-byte encrypted archive. Restore + full three-part verification:
**1.07s**. Both comfortably inside the machine-heat policy's "run it once if it stays under ~2
minutes" allowance for `make restore-check`, and inside §12's "recovery within 4 hours" planning
objective by four orders of magnitude at this dataset size — the dataset here is a demo-scale
synthetic seed, not a production-scale one, so this is evidence the mechanism works correctly and
quickly at this scale, not a claim about restore time at real-factory data volumes.

Manifest excerpt from that run (`manifest.json`, inside the encrypted archive):

```json
{
  "database": "linesense_dev",
  "created_at": "20260920T072330Z",
  "row_counts": {
    "agent_results": 0,
    "agent_tasks": 0,
    "alembic_version": 1,
    "allocations": 153,
    "analysis_runs": 0,
    "approvals": 0,
    "audit_events": 0,
    "bom_lines": 61,
    "bom_versions": 16,
    "customers": 26,
    "cycle_observations": 720,
    "defect_observations": 23
  }
}
```

(`document_versions`/`chunks`/`documents` were 0 in this run: the seeded dataset used for this
exercise had not had `make seed --with-documents` run against it, so the storage-key check had
zero rows to verify — the check itself is exercised end to end by
`tests/agents/test_document_tool.py`/`tests/security/test_prompt_injection.py`, which do upload and
process a real document.)

## Known limitations

- **Restore role/ownership.** `pg_restore` runs as the cluster superuser (not the `linesense_owner`
  migration role) because `vector`/`pg_trgm` are created by the superuser
  (backend-contracts.md §1) and a dump's `COMMENT ON EXTENSION` statements fail with "must be owner
  of extension" under any other role. `--no-owner` is passed, so every restored object ends up owned
  by the superuser rather than `linesense_owner`. That is fine for `linesense_restore` (a
  verification-only target nothing else writes to) but is not how a production restore should leave
  a database meant for ongoing use — a production runbook should re-run
  `ALTER ... OWNER TO linesense_owner` (or restore as a role that already owns the extensions) before
  handing the restored database back to the application.
- **Local-cluster-only.** Both scripts assume the project-local cluster's default roles/passwords
  (overridable via `LS_DB_*` environment variables, matching `scripts/dev-db.sh`'s conventions) and
  are not yet parameterized for a managed/remote PostgreSQL target.
