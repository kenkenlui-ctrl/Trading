#!/bin/bash
# git_housekeeping.sh — remove junk entries from .git/refs/heads/
#
# 2026-10-02: Finder (or any file browser that walks the repo) drops a
# .DS_Store into .git/refs/heads/. git treats every entry in refs/heads/ as
# a ref name, so `git fsck` reports
#     error: refs/heads/.DS_Store: badRefName: invalid refname format
# git keeps working (it falls back to packed-refs), but the noise masks real
# corruption in fsck output, and a junk ref can break `git bundle --all` —
# a past instance was "refs/heads/main 2", a Finder duplicate-suffix accident.
#
# IMPORTANT: a ref's FILENAME is the branch name ("main"), not a hash. The
# 40-hex sha lives in the file CONTENT. An earlier version of this script
# tested the filename and deleted the real `main` ref. The check below reads
# the content; anything that is not exactly one 40-hex line is junk.
set -u
REPO="${1:-/Users/kenken/dev/dsa-hk}"
HEADS="$REPO/.git/refs/heads"
[ -d "$HEADS" ] || { echo "git_housekeeping: no $HEADS, skip"; exit 0; }

removed=0
while IFS= read -r -d '' p; do
  content="$(tr -d ' \t\n\r' < "$p" 2>/dev/null || true)"
  if [ "${#content}" -eq 40 ] && printf '%s' "$content" | grep -qE '^[0-9a-f]{40}$'; then
    continue  # a real loose ref
  fi
  echo "  git_housekeeping: removing junk ref '$(basename "$p")'"
  rm -f "$p" && removed=$((removed+1))
done < <(find "$HEADS" -mindepth 1 -maxdepth 1 -type f ! -name '*.lock' -print0)

if [ "$removed" -gt 0 ]; then
  echo "git_housekeeping: removed $removed junk ref(s)"
else
  echo "git_housekeeping: refs clean"
fi
exit 0
