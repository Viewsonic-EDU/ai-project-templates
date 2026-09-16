#!/usr/bin/env bash
# new-project.sh — copy ONE template out of this monorepo into a fresh directory.
#
#   scripts/new-project.sh <template> <dest>
#   scripts/new-project.sh project-template ~/Documents/code/my-app
#   scripts/new-project.sh project-memory   ~/claude-memory
#
# GitHub's "Use this template" copies a whole repo, which is wrong for a monorepo of
# templates — this script exports a single subdirectory instead. It works from a local
# clone (git archive, so untracked junk is never copied) and leaves <dest> as a plain
# directory with no git history; run `git init` there yourself.
set -euo pipefail

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -eq 2 ] || usage
template="$1"; dest="$2"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# A template is a TOP-LEVEL directory that ships its own README.md (so `scripts/` and nested
# paths are rejected, and the strip-components=1 below is always right).
list_templates() { for d in "$ROOT"/*/; do [ -f "$d/README.md" ] && basename "$d"; done; }
case "$template" in */*|.|..) template="" ;; esac
if [ -z "$template" ] || [ ! -f "$ROOT/$template/README.md" ]; then
  echo "no such template: $1" >&2; echo "available:" >&2; list_templates >&2; exit 1
fi

if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
  echo "refusing: $dest exists and is not empty" >&2; exit 1
fi
mkdir -p "$dest"
dest="$(cd "$dest" && pwd)"

# git archive exports only TRACKED files of the chosen subdirectory.
git -C "$ROOT" archive HEAD "$template" | tar -x -C "$dest" --strip-components=1
echo "==> $template → $dest"
echo "    next: cd '$dest' && cat README.md   # follow its bootstrap / instantiate steps"
