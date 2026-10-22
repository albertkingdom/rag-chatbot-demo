#!/usr/bin/env bash
# PreToolUse gate for Bash calls. Exit 2 blocks the call and returns stderr to Claude.
#
# Gates:
#   1. git add -A / --all / -u / .          stage files one by one
#   2. git commit on main/master/develop    work on a feature branch
#   3. git commit with AI-tool attribution  project rule: commit messages never mention AI tools
#   4. gh pr create / spectra archive       needs a code review recorded for the current HEAD
#
# The review record is written by .claude/hooks/mark-reviewed.sh (see skills/code-review).
set -u

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

deny() {
  printf 'BLOCKED by .claude/hooks/guard-bash.sh: %s\n' "$1" >&2
  exit 2
}

# A command segment starts at the beginning of the line or after ; & | (&& and || included).
seg='(^|[;&|])[[:space:]]*'

if printf '%s' "$cmd" | grep -Eq "${seg}git[[:space:]]+add[[:space:]]+(-A|--all|-u|--update|\.)([[:space:]]|\$)"; then
  deny "stage files by name (git add <file>); 'git add .', '-A' and '-u' can sweep in .env, local state or unrelated changes."
fi

if printf '%s' "$cmd" | grep -Eq "${seg}git[[:space:]]+commit"; then
  branch=$(git symbolic-ref --short -q HEAD || true)
  case "$branch" in
    main|master|develop)
      deny "you are on '$branch'. Create feature/<name> first (git switch -c feature/<name>)." ;;
  esac
  if printf '%s' "$cmd" | grep -Eiq 'co-authored-by|generated with|noreply@anthropic|claude code|chatgpt|copilot'; then
    deny "commit messages must not mention AI tools or carry Co-Authored-By / 'Generated with' lines (CLAUDE.md，Commit 訊息)."
  fi
fi

if printf '%s' "$cmd" | grep -Eq "${seg}(gh[[:space:]]+pr[[:space:]]+create|spectra[[:space:]]+archive)"; then
  head_sha=$(git rev-parse HEAD 2>/dev/null || true)
  marker="${CLAUDE_PROJECT_DIR:-.}/.claude/state/review-passed"
  if [ ! -f "$marker" ] || [ "$(cat "$marker")" != "$head_sha" ]; then
    deny "no code review is recorded for HEAD (${head_sha:0:7}). Run the code-review skill first and resolve blocking and important findings (review-findings skill); it records the result with .claude/hooks/mark-reviewed.sh. New commits invalidate the record, so re-review after fixes."
  fi
fi

exit 0
