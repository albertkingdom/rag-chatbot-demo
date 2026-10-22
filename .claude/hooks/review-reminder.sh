#!/usr/bin/env bash
# Stop hook: when a feature branch has commits that no code review covers, ask Claude to run
# the review (or tell the user it is pending) before it stops. Fires at most once per HEAD,
# so it cannot loop and it stays quiet while the work is still in progress.
set -u

input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

branch=$(git symbolic-ref --short -q HEAD || true)
case "$branch" in ""|main|master|develop) exit 0 ;; esac

base=$(git symbolic-ref --short -q refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
if [ -z "$base" ]; then
  for b in main master; do git rev-parse --verify -q "$b" >/dev/null && base=$b && break; done
fi
[ -z "$base" ] && exit 0

ahead=$(git rev-list --count "$base"..HEAD 2>/dev/null || echo 0)
[ "$ahead" -gt 0 ] || exit 0

head_sha=$(git rev-parse HEAD)
state=.claude/state
[ "$(cat "$state/review-passed" 2>/dev/null)" = "$head_sha" ] && exit 0
[ "$(cat "$state/review-nagged" 2>/dev/null)" = "$head_sha" ] && exit 0

# Only code changes need a review; skip branches that touch nothing but docs and local config.
if ! git diff --name-only "$base"...HEAD | grep -Eq '^(src|tests|frontend|scripts|terraform|fixlab|bench|Dockerfile|docker-compose|requirements|pyproject)'; then
  exit 0
fi

mkdir -p "$state"
printf '%s' "$head_sha" > "$state/review-nagged"

jq -n --arg n "$ahead" --arg b "$base" --arg s "${head_sha:0:7}" '{
  decision: "block",
  reason: ("Branch is \($n) commit(s) ahead of \($b) and HEAD \($s) has no recorded code review. If this work is ready for a PR, run the code-review skill now and resolve blocking and important findings with review-findings. If it is not ready, say so in your final message and tell the user the review is still pending. gh pr create is blocked until a review is recorded.")
}'
