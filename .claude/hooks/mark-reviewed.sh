#!/usr/bin/env bash
# Record that HEAD passed code review (no open blocking or important findings).
# Called by the project-review and review-findings skills; guard-bash.sh checks the record
# before 'gh pr create'. The record is local and git-ignored. Works from any subdirectory.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"

cd "$(git rev-parse --show-toplevel)"
export CLAUDE_PROJECT_DIR="$PWD"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Tracked files have uncommitted changes. Commit them first, so the review record matches HEAD." >&2
  exit 1
fi

mkdir -p .claude/state
git rev-parse HEAD > .claude/state/review-passed
echo "Review recorded for $(git rev-parse --short HEAD)."
