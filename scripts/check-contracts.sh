#!/usr/bin/env bash
# Verify the committed API contract and generated web client are current.
#
# 1. Export the backend's OpenAPI document to a temp file (same export as
#    scripts/export-openapi.sh) and compare it with contracts/openapi.json.
# 2. Regenerate apps/web/src/generated/api.ts from contracts/openapi.json into a
#    temp file and compare it with the committed TypeScript.
# 3. Fail on uncommitted changes to either file (`git diff --exit-code`).
#
# Nothing in the working tree is modified. Fix failures with `make contracts`
# and commit both files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTRACT="$REPO_ROOT/contracts/openapi.json"
WEB_DIR="$REPO_ROOT/apps/web"
GENERATED="$WEB_DIR/src/generated/api.ts"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
status=0

# Keep in step with scripts/export-openapi.sh (same export command, sorted keys).
(cd "$REPO_ROOT/services/backend" && uv run python -c "
import json
from app.main import create_app
print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))
") > "$tmp_dir/openapi.json"

if ! diff -u "$CONTRACT" "$tmp_dir/openapi.json" > "$tmp_dir/openapi.diff"; then
  echo "contracts-check: contracts/openapi.json does not match the backend routes (run 'make contracts')." >&2
  head -n 60 "$tmp_dir/openapi.diff" >&2
  status=1
fi

(cd "$WEB_DIR" && npx --no-install openapi-typescript "$CONTRACT" -o "$tmp_dir/api.ts" > /dev/null)
if ! diff -u "$GENERATED" "$tmp_dir/api.ts" > "$tmp_dir/api.diff"; then
  echo "contracts-check: apps/web/src/generated/api.ts is stale (run 'make contracts')." >&2
  head -n 60 "$tmp_dir/api.diff" >&2
  status=1
fi

if ! git -C "$REPO_ROOT" diff --exit-code --stat -- contracts/openapi.json apps/web/src/generated/api.ts; then
  echo "contracts-check: contract files have uncommitted changes." >&2
  status=1
fi

if [[ $status -eq 0 ]]; then
  echo "contracts-check: OpenAPI contract and generated web client are up to date."
fi
exit "$status"
