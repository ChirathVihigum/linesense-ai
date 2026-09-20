#!/usr/bin/env bash
# Self-test for scripts/dependency-audit.sh (task-25 review round 1, item
# 3): proves the script's own exit-code plumbing is correct -- it fails
# when either tool reports a finding, and passes when both are clean --
# without hitting the network (no real `pip-audit`/`npm audit` call): a
# fake `uvx`/`npm` is put ahead of the real ones on PATH, each just echoing
# and exiting with the code the case needs. `uv export` still runs for
# real (a local lockfile resolve, not a network call).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPENDENCY_AUDIT="$SCRIPT_DIR/dependency-audit.sh"

log() { echo "[self-test-dependency-audit] $*" >&2; }

FAILED=0

_fake_bin_dir() {
  local dir uvx_exit npm_exit
  dir="$(mktemp -d)"
  uvx_exit="$1"
  npm_exit="$2"
  cat >"$dir/uvx" <<EOF
#!/usr/bin/env bash
echo "[fake uvx] pip-audit stub, exiting $uvx_exit"
exit $uvx_exit
EOF
  cat >"$dir/npm" <<EOF
#!/usr/bin/env bash
echo "[fake npm] audit stub, exiting $npm_exit"
exit $npm_exit
EOF
  chmod +x "$dir/uvx" "$dir/npm"
  echo "$dir"
}

_run_with_fakes() {
  local uvx_exit="$1" npm_exit="$2"
  local fake_bin
  fake_bin="$(_fake_bin_dir "$uvx_exit" "$npm_exit")"
  set +e
  PATH="$fake_bin:$PATH" bash "$DEPENDENCY_AUDIT" >/tmp/self-test-dependency-audit.out 2>&1
  status=$?
  set -e
  rm -rf "$fake_bin"
  echo "$status"
}

_assert_status() {
  local uvx_exit="$1" npm_exit="$2" expected="$3" label="$4"
  local actual
  actual="$(_run_with_fakes "$uvx_exit" "$npm_exit")"
  if [ "$actual" -eq "$expected" ]; then
    log "OK: $label (uvx=$uvx_exit npm=$npm_exit -> exit $actual, expected $expected)"
  else
    log "FAIL: $label (uvx=$uvx_exit npm=$npm_exit -> exit $actual, expected $expected)"
    cat /tmp/self-test-dependency-audit.out >&2
    FAILED=1
  fi
}

_assert_status 0 0 0 "both clean passes"
_assert_status 1 0 1 "pip-audit finding fails the script"
_assert_status 0 1 1 "npm audit finding fails the script"
_assert_status 1 1 1 "both findings still fails the script (not just the first)"

if [ "$FAILED" -ne 0 ]; then
  log "SELF-TEST FAILED: scripts/dependency-audit.sh does not propagate exit codes correctly"
  exit 1
fi
log "SELF-TEST PASSED: scripts/dependency-audit.sh propagates both tools' exit codes correctly"
