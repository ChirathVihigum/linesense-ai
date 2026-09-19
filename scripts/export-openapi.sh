#!/usr/bin/env bash
# Export the backend's OpenAPI schema to contracts/openapi.json.
#
# Deterministic output: `Settings()` does read process/`.env` environment
# variables (as it always does), but none of them change which routes exist
# or their request/response schemas (only connection strings, secrets, and
# similar runtime config do, none of which reach the OpenAPI document), and
# the app is built without touching the database. Combined with sorted JSON
# keys, re-running this against unchanged routes produces a byte-identical
# file (safe to commit and diff in review) regardless of environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/services/backend"
OUT_FILE="$REPO_ROOT/contracts/openapi.json"

mkdir -p "$REPO_ROOT/contracts"

cd "$BACKEND_DIR"
uv run python -c "
import json
from app.main import create_app
print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))
" > "$OUT_FILE"

echo "Wrote $OUT_FILE"
