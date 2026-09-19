#!/usr/bin/env bash
# Serialize resource-heavy commands (tests, builds, installs, seeding, eval)
# across all local agents so only one runs at a time (machine heat policy).
# Usage: scripts/heavy-job.sh <command> [args...]
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
lock_dir="$repo_root/.local/heavy-job.lock"
mkdir -p "$repo_root/.local"
waited=0
until mkdir "$lock_dir" 2>/dev/null; do
  # Recover a lock left behind by a process that no longer exists.
  if [[ -f "$lock_dir/pid" ]] && ! kill -0 "$(cat "$lock_dir/pid")" 2>/dev/null; then
    rm -rf "$lock_dir"
    continue
  fi
  if (( waited % 60 == 0 )); then
    echo "heavy-job: waiting for $(cat "$lock_dir/cmd" 2>/dev/null || echo 'another job')" >&2
  fi
  sleep 10
  waited=$((waited + 10))
  if (( waited >= 3600 )); then
    echo "heavy-job: gave up after 60 minutes waiting for the lock" >&2
    exit 75
  fi
done
echo $$ > "$lock_dir/pid"
printf '%s\n' "$*" > "$lock_dir/cmd"
trap 'rm -rf "$lock_dir"' EXIT INT TERM
"$@"
