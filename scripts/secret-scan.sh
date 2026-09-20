#!/usr/bin/env bash
# Scan every tracked file for high-risk secret patterns (task-25-brief.md
# req. 10). Fails (non-zero exit) on any hit, and on any scanning error
# (never fails open). Run by `make security`.
#
# Usage: scripts/secret-scan.sh [repo-root]
#   repo-root defaults to this script's own repository (a git worktree),
#   overridable so scripts/self-test-secret-scan.sh can point this at a
#   throwaway temp repo with planted fixtures.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="${1:-$DEFAULT_REPO_ROOT}"
cd "$REPO_ROOT"

log() { echo "[secret-scan] $*" >&2; }

FOUND=0
ALL_FILES="$(git ls-files)"

# Specific, reviewed test fixtures that deliberately contain a fake
# `sk-ant-...`-shaped string, to test *this project's own*
# redaction/error-handling code, plus the one doc that quotes those same
# fixture values verbatim for auditability (see
# docs/security/scan-results.md's triage entry for the full reasoning).
# This is the ONLY exemption from the key/AWS-id/private-key patterns
# below -- nothing else under tests/ (or anywhere else) is exempt, so a
# real secret accidentally committed under a test path is still caught.
SK_ANT_ALLOWLIST=(
  "services/backend/tests/unit/test_redaction.py"
  "services/backend/tests/unit/test_anthropic_client.py"
  "services/backend/tests/unit/test_llm_factory.py"
  "docs/security/scan-results.md"
)
SK_ANT_PATTERN='sk-ant-[A-Za-z0-9_-]{10,}'

is_sk_ant_allowlisted() {
  local file="$1"
  local allowed
  for allowed in "${SK_ANT_ALLOWLIST[@]}"; do
    [ "$file" = "$allowed" ] && return 0
  done
  return 1
}

# --- hardcoded password-looking assignments --------------------------------
# Runs before the key/AWS-id/private-key patterns below (order is otherwise
# arbitrary -- either loop aborts the whole script on any file it cannot
# read) specifically so scripts/self-test-secret-scan.sh's unreadable-file
# case exercises *this* branch's error handling end to end, since that is
# the one item-1/round-2 review fixed: an unreadable file is always caught
# by whichever loop reaches it first, and this is now first.
# `password\s*=\s*['"][^'"]{8,}`, excluding .env.example/tests/ (documented
# fixture or dev-default values there) per task-25-brief.md req. 10 -- this
# is the only pattern the brief itself scopes that exclusion to -- and
# excluding a matched value that is itself a shell/env variable reference
# (`"$SOME_VAR"`) rather than a literal -- e.g. this repo's own
# `PGPASSWORD="$OWNER_PASSWORD"` in scripts/backup.sh/restore.sh, which is
# exactly the pattern the .env.example/tests exclusion is meant to express
# for shell scripts (nothing there is a literal secret).
#
# This used to be `grep ... | grep -v ...` with the combined pipeline's
# `$?` checked for "a real error". Under `pipefail`, a pipeline's exit
# status is only reliably the *first* non-zero stage when every stage
# *after* it succeeds (exits 0); when a later stage also exits non-zero --
# exactly what `grep -v` does whenever it excludes every line, including
# on the empty input a failed first grep produces -- pipefail resolves to
# that later (rightmost) non-zero status instead, masking the first grep's
# real error as an ordinary "no match" (verified: `(exit 2) | (exit 1)`
# under `set -o pipefail` returns 1, not 2). `${PIPESTATUS[@]}` does not
# help either, because the pipe runs inside a `$(...)` command
# substitution's own subshell, whose PIPESTATUS is gone once that subshell
# exits. So there is only one grep call here now; the second stage (drop a
# `="$VAR"` shell-reference match) is plain bash pattern matching, with
# nothing left to pipe into and no exit status left to lose.
SHELL_VAR_VALUE_RE="=[[:space:]]*['\"]\\\$"
PASSWORD_HAS_HITS=0
while IFS= read -r file; do
  [ -z "$file" ] && continue
  case "$file" in
  .env.example | tests/* | */tests/*) continue ;;
  esac
  set +e
  raw_matches="$(grep -nEi -- "password[[:space:]]*=[[:space:]]*['\"][^'\"]{8,}" "$file")"
  grep_status=$?
  set -e
  case "$grep_status" in
  0 | 1) ;; # 0: matched; 1: no match. Both expected outcomes.
  *)
    log "grep failed (exit $grep_status) scanning '$file' for password-looking assignments"
    exit 2
    ;;
  esac

  matches=""
  if [ "$grep_status" -eq 0 ]; then
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      if [[ "$line" =~ $SHELL_VAR_VALUE_RE ]]; then
        continue # a "...=\"$VAR\"" shell/env reference, not a literal value
      fi
      matches="${matches}${line}"$'\n'
    done <<<"$raw_matches"
  fi

  if [ -n "$matches" ]; then
    if [ "$PASSWORD_HAS_HITS" -eq 0 ]; then
      log "password-looking assignment(s) found outside .env.example/tests:"
      PASSWORD_HAS_HITS=1
    fi
    printf '%s' "$matches" | sed "s#^#  $file:#" >&2
    FOUND=1
  fi
done <<<"$ALL_FILES"

# --- Anthropic keys, AWS access key ids, PEM private key headers ----------
# Self-note: these three patterns each require several specific literal
# characters right after their fixed prefix ("AKIA", "sk-ant-", "-----BEGIN
# ("); this file's own PATTERNS array below is not itself a match, since a
# regex's source text (brackets, quantifiers, alternation bars) is not the
# character class/alternative it describes.
#
# Every pattern is matched with `grep -E -e "$pattern" --`: the leading `-e`
# is required because the PEM pattern starts with a literal `-`
# ("-----BEGIN ..."), which `grep '-----BEGIN...' file` would otherwise
# parse as a (nonexistent) option and fail immediately -- silently, if that
# failure's exit status were swallowed. Every grep call below is run with
# `set +e`/`set -e` bracketing it so its real exit status is captured: 0
# (matched) and 1 (no match) are both expected outcomes; anything else (2:
# a real error, e.g. a bad pattern or an unreadable file) aborts the whole
# script non-zero rather than being treated as "no match found".
PATTERNS=(
  "$SK_ANT_PATTERN"
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----'
)
for pattern in "${PATTERNS[@]}"; do
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    set +e
    grep -qE -e "$pattern" -- "$file"
    status=$?
    set -e
    case "$status" in
    0)
      if [ "$pattern" = "$SK_ANT_PATTERN" ] && is_sk_ant_allowlisted "$file"; then
        continue
      fi
      log "pattern '$pattern' matched in: $file"
      FOUND=1
      ;;
    1) ;; # no match: fine
    *)
      log "grep failed (exit $status) scanning '$file' for pattern '$pattern'"
      exit 2
      ;;
    esac
  done <<<"$ALL_FILES"
done

if [ "$FOUND" -ne 0 ]; then
  log "FAILED: high-risk secret pattern(s) found in tracked files"
  exit 1
fi

log "OK: no high-risk secret patterns found in $(echo "$ALL_FILES" | wc -l | tr -d ' ') tracked files"
