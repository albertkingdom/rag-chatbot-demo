#!/usr/bin/env bash
# Shared helpers for the hooks in this directory (sourced, not executed).
# Project-specific values are the three variables at the top; everything else is generic.

# Paths whose change means a branch needs a recorded code review. A branch that only touches
# documentation and specs is exempt from the review gate.
CODE_RE='^(src/|tests/|frontend/|scripts/|terraform/|Dockerfile|docker-compose|requirements|\.github/|\.claude/hooks/)'
# Files an archive commit may touch after a review without invalidating it.
SPEC_RE='^(openspec/|\.spectra/)'
# 1 = 'spectra archive' also needs a recorded review (the project requires review before archive).
GATE_ARCHIVE=0

PROTECTED_BRANCHES="main master develop"

root_dir() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then printf '%s' "$CLAUDE_PROJECT_DIR"; return; fi
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

is_protected() {
  local b
  for b in $PROTECTED_BRANCHES; do [ "$1" = "$b" ] && return 0; done
  return 1
}

# The branch work is compared against: origin's default branch, else a local main/master.
base_ref() {
  local b c
  b=$(git symbolic-ref --short -q refs/remotes/origin/HEAD 2>/dev/null || true)
  if [ -n "$b" ] && git rev-parse -q --verify "$b" >/dev/null 2>&1; then printf '%s' "$b"; return; fi
  for c in origin/main origin/master main master; do
    if git rev-parse -q --verify "$c" >/dev/null 2>&1; then printf '%s' "$c"; return; fi
  done
}

# Succeeds when the branch changes code (or the base is unknown, so we cannot tell).
touches_code() {
  local base
  base=$(base_ref)
  [ -n "$base" ] || return 0
  git diff --name-only "$base"...HEAD 2>/dev/null | grep -Eq "$CODE_RE"
}

# Succeeds when the recorded review still covers HEAD: the record is HEAD itself, or an ancestor
# with only spec/archive files changed since (what 'spectra archive' commits).
reviewed_ok() {
  local marker m head changed
  marker="$(root_dir)/.claude/state/review-passed"
  [ -f "$marker" ] || return 1
  m=$(cat "$marker")
  head=$(git rev-parse HEAD 2>/dev/null) || return 1
  [ "$m" = "$head" ] && return 0
  git merge-base --is-ancestor "$m" "$head" 2>/dev/null || return 1
  changed=$(git diff --name-only "$m" "$head" 2>/dev/null)
  [ -n "$changed" ] || return 1
  if printf '%s\n' "$changed" | grep -Ev "$SPEC_RE" | grep -q .; then return 1; fi
  return 0
}
