#!/usr/bin/env bash
# Export the backend's OpenAPI schema to contracts/openapi.json.
#
# Deterministic output: the app is built without touching the database or
# reading local environment overrides (default `Settings()`), and the JSON
# keys are sorted, so re-running this against unchanged routes produces a
# byte-identical file (safe to commit and diff in review).
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
