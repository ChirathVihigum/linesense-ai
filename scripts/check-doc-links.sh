#!/usr/bin/env bash
# Verify that every relative markdown link target in docs/, README.md, and
# CLAUDE.md resolves to a file that actually exists on disk. Absolute URLs
# (http/https), mailto: links, and pure in-page fragments (#heading) are
# skipped. Exits non-zero and prints one "BROKEN LINK" line per bad target.
#
# Usage: scripts/check-doc-links.sh [root-dir]
#   root-dir defaults to the repository root (the parent of this script's
#   directory). A root-dir argument is accepted so this script can be
#   exercised against a throwaway fixture tree in tests.
set -euo pipefail

root_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$root_dir"

files=()
[ -f README.md ] && files+=("README.md")
[ -f CLAUDE.md ] && files+=("CLAUDE.md")
if [ -d docs ]; then
  while IFS= read -r -d '' f; do
    files+=("$f")
  done < <(find docs -type f -name '*.md' -print0 | sort -z)
fi

if [ "${#files[@]}" -eq 0 ]; then
  echo "check-doc-links: no markdown files found under $root_dir"
  exit 0
fi

status=0
checked=0

for file in "${files[@]}"; do
  dir="$(dirname "$file")"
  # Extract every "](...)" link-target group in the file, one per line.
  while IFS= read -r target; do
    [ -z "$target" ] && continue
    case "$target" in
      http://* | https://* | mailto:* | "#"*) continue ;;
    esac
    # Strip a trailing #fragment (e.g. "glossary.md#sam" -> "glossary.md").
    path_part="${target%%#*}"
    [ -z "$path_part" ] && continue
    # Absolute filesystem paths are not relative doc links; skip them.
    case "$path_part" in
      /*) continue ;;
    esac
    checked=$((checked + 1))
    resolved="$dir/$path_part"
    if [ ! -e "$resolved" ]; then
      echo "BROKEN LINK: $file -> $target (resolved: $resolved)"
      status=1
    fi
  done < <(grep -oE '\]\(([^)]+)\)' "$file" | sed -E 's/^\]\((.*)\)$/\1/')
done

echo "check-doc-links: checked $checked relative link target(s) across ${#files[@]} file(s)"
exit "$status"
