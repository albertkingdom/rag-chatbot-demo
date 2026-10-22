#!/usr/bin/env bash
# Record that HEAD passed code review (no open blocking or important findings).
# Called by the code-review and review-findings skills; guard-bash.sh checks the record
# before 'gh pr create' and 'spectra archive'. The record is local and git-ignored.
set -eu

cd "${CLAUDE_PROJECT_DIR:-.}"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Tracked files have uncommitted changes. Commit them first, so the review record matches HEAD." >&2
  exit 1
fi

mkdir -p .claude/state
git rev-parse HEAD > .claude/state/review-passed
echo "Review recorded for $(git rev-parse --short HEAD)."
