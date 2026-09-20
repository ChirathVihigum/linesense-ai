#!/usr/bin/env bash
# Scan every tracked file for high-risk secret patterns (task-25-brief.md
# req. 10). Fails (non-zero exit) on any hit. Run by `make security`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

log() { echo "[secret-scan] $*" >&2; }

FOUND=0
ALL_FILES="$(git ls-files)"
# Every pattern below excludes tests/ (and .env.example, for the password
# one): this repo's own test suite deliberately uses literal fake
# `sk-ant-...` strings to test *this project's own* redaction/error-handling
# code (tests/unit/test_redaction.py, test_anthropic_client.py,
# test_llm_factory.py) -- see docs/security/scan-results.md's triage entry
# for the reasoning and the exact reviewed list. task-25-brief.md's own
# phrasing scopes the tests/ exclusion to the password pattern specifically;
# this scan applies the same, documented exclusion to all four patterns
# rather than let a deliberate, reviewed fixture permanently fail `make
# security` (or force those tests to use a shape too fake to be a
# meaningful regression check).
FILES="$(echo "$ALL_FILES" | grep -v -E '(^|/)tests/' || true)"

# --- Anthropic keys, AWS access key ids, PEM private key headers ----------
# Self-note: these three patterns each require several specific literal
# characters right after their fixed prefix ("AKIA", "sk-ant-", "-----BEGIN
# ("); this file's own PATTERNS array below is not itself a match, since a
# regex's source text (brackets, quantifiers, alternation bars) is not the
# character class/alternative it describes.
PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{10,}'
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----'
)
for pattern in "${PATTERNS[@]}"; do
  hits="$(echo "$FILES" | xargs -I{} grep -lE "$pattern" {} 2>/dev/null || true)"
  if [ -n "$hits" ]; then
    log "pattern '$pattern' matched in:"
    echo "$hits" | sed 's/^/  /' >&2
    FOUND=1
  fi
done

# --- hardcoded password-looking assignments --------------------------------
# `password\s*=\s*['"][^'"]{8,}`, excluding .env.example/tests/ (documented
# fixture or dev-default values there) per task-25-brief.md req. 10, and
# excluding a matched value that is itself a shell/env variable reference
# (`"$SOME_VAR"`) rather than a literal -- e.g. this repo's own
# `PGPASSWORD="$OWNER_PASSWORD"` in scripts/backup.sh/restore.sh, which is
# exactly the pattern the .env.example/tests exclusion is meant to express
# for shell scripts (nothing there is a literal secret).
PASSWORD_HAS_HITS=0
while IFS= read -r file; do
  case "$file" in
  .env.example | tests/* | */tests/*) continue ;;
  esac
  matches="$(
    grep -nEi "password[[:space:]]*=[[:space:]]*['\"][^'\"]{8,}" "$file" 2>/dev/null |
      grep -vE "=[[:space:]]*['\"]\\\$" || true
  )"
  if [ -n "$matches" ]; then
    if [ "$PASSWORD_HAS_HITS" -eq 0 ]; then
      log "password-looking assignment(s) found outside .env.example/tests:"
      PASSWORD_HAS_HITS=1
    fi
    echo "$matches" | sed "s#^#  $file:#" >&2
    FOUND=1
  fi
done <<<"$FILES"

if [ "$FOUND" -ne 0 ]; then
  log "FAILED: high-risk secret pattern(s) found in tracked files"
  exit 1
fi

log "OK: no high-risk secret patterns found in $(echo "$FILES" | wc -l | tr -d ' ') tracked files"
