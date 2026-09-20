#!/usr/bin/env bash
# Self-test for scripts/secret-scan.sh (task-25 review round 1, item 3):
# proves "0 findings" means something by planting one of each pattern in a
# throwaway git repo and asserting the scanner actually fails on it, then
# asserting a clean/exempt repo passes. Exits non-zero (with a diagnostic)
# if either scanner behaviour regresses.
#
# Every planted value below is assembled from two halves that are never
# adjacent in *this file's own source text* (each half is its own quoted
# string, on its own line) -- otherwise this file would itself be a
# planted secret the real `scripts/secret-scan.sh` run over this repository
# would (correctly) flag.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRET_SCAN="$SCRIPT_DIR/secret-scan.sh"

log() { echo "[self-test-secret-scan] $*" >&2; }

FAILED=0

_make_repo() {
  local dir
  dir="$(mktemp -d)"
  (cd "$dir" && git init -q && git config user.email test@example.test && git config user.name test)
  echo "$dir"
}

_assert_fails() {
  local dir="$1" label="$2"
  set +e
  bash "$SECRET_SCAN" "$dir" >/tmp/self-test-secret-scan.out 2>&1
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    log "FAIL: expected secret-scan.sh to fail for '$label', but it exited 0"
    cat /tmp/self-test-secret-scan.out >&2
    FAILED=1
  else
    log "OK: secret-scan.sh correctly failed for '$label' (exit $status)"
  fi
}

_assert_passes() {
  local dir="$1" label="$2"
  set +e
  bash "$SECRET_SCAN" "$dir" >/tmp/self-test-secret-scan.out 2>&1
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    log "FAIL: expected secret-scan.sh to pass for '$label', but it exited $status"
    cat /tmp/self-test-secret-scan.out >&2
    FAILED=1
  else
    log "OK: secret-scan.sh correctly passed for '$label'"
  fi
}

# --- planted AWS access key id ---------------------------------------------
aws_key_prefix="AKIA"
aws_key_suffix="ABCDEFGHIJKLMNOP"
dir="$(_make_repo)"
printf '%s%s\n' "$aws_key_prefix" "$aws_key_suffix" >"$dir/leak.txt"
(cd "$dir" && git add leak.txt)
_assert_fails "$dir" "planted AWS access key id"
rm -rf "$dir"

# --- planted PEM private key header (the pattern that starts with "-") ----
pem_head="-----BEGIN RSA"
pem_tail=" PRIVATE KEY-----"
dir="$(_make_repo)"
printf '%s%s\nMIIBOgIBAAJBAK...\n-----END RSA PRIVATE KEY-----\n' "$pem_head" "$pem_tail" \
  >"$dir/id_rsa"
(cd "$dir" && git add id_rsa)
_assert_fails "$dir" "planted PEM private key header"
rm -rf "$dir"

# --- planted sk-ant token, NOT one of the reviewed allowlisted paths -------
sk_ant_prefix="sk-ant-"
sk_ant_suffix="thisIsNotOnTheAllowlist123"
dir="$(_make_repo)"
printf 'ANTHROPIC_API_KEY=%s%s\n' "$sk_ant_prefix" "$sk_ant_suffix" >"$dir/leak.env"
(cd "$dir" && git add leak.env)
_assert_fails "$dir" "planted sk-ant token outside the allowlist"
rm -rf "$dir"

# --- planted hardcoded password assignment ---------------------------------
pw_key="password"
pw_val="SuperSecret1"
dir="$(_make_repo)"
printf '%s = "%s"\n' "$pw_key" "$pw_val" >"$dir/config.py"
(cd "$dir" && git add config.py)
_assert_fails "$dir" "planted password assignment"
rm -rf "$dir"

# --- a password assignment under .env.example / tests/ is still exempt ----
pw_key_upper="PASSWORD"
dir="$(_make_repo)"
mkdir -p "$dir/tests"
printf '%s = "%s"\n' "$pw_key" "$pw_val" >"$dir/tests/fixture.py"
printf 'LS_DEV_IDP_%s=demo-password\n' "$pw_key_upper" >"$dir/.env.example"
(cd "$dir" && git add tests/fixture.py .env.example)
_assert_passes "$dir" "password assignment under tests//.env.example (documented exemption)"
rm -rf "$dir"

# --- a shell-variable password reference is not a literal secret ----------
dir="$(_make_repo)"
printf 'PG%s="$OWNER_%s"\n' "$pw_key_upper" "$pw_key_upper" >"$dir/backup.sh"
(cd "$dir" && git add backup.sh)
_assert_passes "$dir" "PGPASSWORD=\"\$VAR\" shell reference"
rm -rf "$dir"

# --- clean repo passes ------------------------------------------------------
dir="$(_make_repo)"
printf '# Just a README\n' >"$dir/README.md"
(cd "$dir" && git add README.md)
_assert_passes "$dir" "clean repo"
rm -rf "$dir"

# --- a file grep genuinely cannot read must abort, not read as "no match" -
# task-25 review round 2: the password-assignment branch used to pipe two
# greps under `pipefail`, so a real read error in the *first* grep could be
# masked by the *second* grep's own "no lines matched" exit status (1) --
# fixed by removing the pipe entirely. secret-scan.sh now runs the
# password-assignment loop *first* specifically so this case exercises that
# branch's error handling end to end (whichever loop reaches an unreadable
# file first aborts the whole script; ordering is otherwise arbitrary).
# `chmod 000` is enough to make a regular file unreadable to its own owner
# on both Linux and macOS, without needing root.
dir="$(_make_repo)"
printf 'irrelevant content\n' >"$dir/unreadable.txt"
(cd "$dir" && git add unreadable.txt) # must add while still readable
chmod 000 "$dir/unreadable.txt"
if [ -r "$dir/unreadable.txt" ]; then
  # Running as root (some CI containers do): chmod 000 does not block root's
  # own reads, so this case cannot be exercised meaningfully here. Skip
  # rather than assert something that would not actually test anything.
  log "SKIP: unreadable-file case (running as a user chmod 000 cannot block, e.g. root)"
else
  _assert_fails "$dir" "a file grep cannot read (real error, not a match/no-match)"
fi
chmod 700 "$dir/unreadable.txt" # so `rm -rf` can actually remove it
rm -rf "$dir"

if [ "$FAILED" -ne 0 ]; then
  log "SELF-TEST FAILED: scripts/secret-scan.sh did not behave as expected"
  exit 1
fi
log "SELF-TEST PASSED: scripts/secret-scan.sh behaves correctly on every planted case"
