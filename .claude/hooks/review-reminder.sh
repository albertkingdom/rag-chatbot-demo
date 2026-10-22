#!/usr/bin/env bash
# Stop hook: when a feature branch has code commits that no code review covers, ask Claude to run
# the review (or tell the user it is pending) before it stops. Fires at most once per HEAD, so it
# cannot loop and stays quiet while the work is in progress. Uses the same code/docs rule as the
# gh pr create gate in guard-bash.sh. Requires jq.
set -u

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" = "true" ] && exit 0

init_repo "$input" || exit 0

branch=$(git symbolic-ref --short -q HEAD || true)
[ -n "$branch" ] || exit 0
is_protected "$branch" && exit 0

base=$(base_ref)
[ -n "$base" ] || exit 0
ahead=$(git rev-list --count "$base"..HEAD 2>/dev/null || echo 0)
[ "$ahead" -gt 0 ] || exit 0

touches_code || exit 0
reviewed_ok && exit 0

head_sha=$(git rev-parse HEAD)
state="$(root_dir)/.claude/state"
[ "$(cat "$state/review-nagged" 2>/dev/null)" = "$head_sha" ] && exit 0

mkdir -p "$state"
printf '%s' "$head_sha" > "$state/review-nagged"

jq -n --arg n "$ahead" --arg b "$base" --arg s "${head_sha:0:7}" '{
  decision: "block",
  reason: ("Branch is \($n) commit(s) ahead of \($b), changes code, and HEAD \($s) has no recorded code review. If this work is ready for a PR, run the project-review skill now and resolve blocking and important findings with review-findings. If it is not ready, say so in your final message and tell the user the review is still pending. gh pr create is blocked until a review is recorded.")
}'
