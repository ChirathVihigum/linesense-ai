#!/usr/bin/env bash
# Dependency vulnerability audit (task-25-brief.md req. 10): the backend's
# exported requirements via `uvx pip-audit`, and the web app's production
# dependencies via `npm audit`. Fails (non-zero exit) if either tool finds
# an unaddressed advisory (npm: high or above). Run by `make security`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/services/backend"
WEB_DIR="$REPO_ROOT/apps/web"

log() { echo "[dependency-audit] $*" >&2; }

FAILED=0

log "backend: exporting requirements and running pip-audit"
REQUIREMENTS_FILE="$(mktemp)"
trap 'rm -f "$REQUIREMENTS_FILE"' EXIT
(cd "$BACKEND_DIR" && uv export --no-hashes --format requirements-txt >"$REQUIREMENTS_FILE")
if ! uvx pip-audit --requirement "$REQUIREMENTS_FILE"; then
  log "backend: pip-audit reported unaddressed vulnerabilities"
  FAILED=1
fi

log "web: npm audit (production dependencies, high severity and above)"
if ! (cd "$WEB_DIR" && npm audit --omit=dev --audit-level=high); then
  log "web: npm audit reported unaddressed vulnerabilities"
  FAILED=1
fi

if [ "$FAILED" -ne 0 ]; then
  log "FAILED: see the findings above and docs/security/scan-results.md for triage"
  exit 1
fi

log "OK: no unaddressed vulnerabilities found"
