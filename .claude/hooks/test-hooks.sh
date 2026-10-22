#!/usr/bin/env bash
# Regression tests for the hooks. Builds a throwaway git repository (with a bare origin) in a temp
# directory, feeds the scripts simulated tool input and checks exit codes and output.
# Run: .claude/hooks/test-hooks.sh        (needs git and jq; touches nothing in the real repository)
set -u

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"
GUARD="$here/guard-bash.sh"; STOP="$here/review-reminder.sh"; MARK="$here/mark-reviewed.sh"; DOCS="$here/remind-docs.sh"

# Per-project paths: a code file, a spec file, a documentation file, a file remind-docs.sh watches.
CODE_FILE=src/app.py; SPEC_FILE=openspec/s.md; DOC_FILE=docs/a.md; REMIND_FILE=src/cache_service.py

command -v jq >/dev/null || { echo "jq is required to run these tests"; exit 2; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
git init -q --bare "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/repo" 2>/dev/null
cd "$tmp/repo" || exit 2
export CLAUDE_PROJECT_DIR="$tmp/repo"
git checkout -q -b master 2>/dev/null || git checkout -q master
mkdir -p "$(dirname "$CODE_FILE")"; echo a > README.md; echo a > "$CODE_FILE"
git add README.md "$CODE_FILE" && git commit -q -m "chore: base"
git push -q origin master 2>/dev/null; git remote set-head origin master >/dev/null 2>&1

pass=0; fail=0
check() { # check <want-rc> <label> <got-rc>
  if [ "$1" = "$3" ]; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL (want rc=$1, got rc=$3): $2"; fi
}
guard() { printf '%s' "$(jq -n --arg c "$1" '{tool_input:{command:$c}}')" | "$GUARD" >/dev/null 2>&1; }
expect() { guard "$2"; check "$1" "$2" "$?"; }
commit_file() { mkdir -p "$(dirname "$1")"; echo "$RANDOM" >> "$1"; git add "$1"; git commit -q -m "chore: change $1"; }
branch() { git checkout -q master; git branch -q -D "$1" 2>/dev/null; git checkout -q -b "$1"; }
unmark() { rm -rf "$tmp/repo/.claude/state"; }

echo "== staging"
branch feature/code; commit_file "$CODE_FILE"; unmark
for c in 'git add -A' 'git add .' 'git add --all' 'git add -u' 'git add --update' 'git -C . add -A' 'git -C /tmp add .' \
  'git -c user.name=x add .' 'git add -f .' 'git add -Av' 'git add -vA' 'git add -f -A' 'git add -- .' 'git add ./' 'git add src .' \
  'git add "."' "git add '.'" 'git add .;ls' '(git add .)' '{ git add .; }' 'if true; then git add .; fi' 'sudo git add .' \
  'FOO=1 git add .' 'env git add .' 'cd x && git add .' 'echo hi | git add .' 'git  add  .' 'git stage .' 'git commit -a -m x' \
  'git commit -am x' 'bash -c "git add -A"' 'git add a b -A'; do expect 2 "$c"; done
for c in 'git add src/app.py' 'git add .claude/x.sh && git status' 'git add ./foo' 'git add .gitignore' 'git add .env' 'git add src/.' \
  'git add foo.' 'echo "git add -A"' 'git add a -n' 'git log --grep="git add ."' 'git status' 'git diff --stat'; do expect 0 "$c"; done

echo "== commit"
expect 0 'git commit -m "docs: add hooks"'
expect 0 'git commit --amend --no-edit'
expect 0 'git commit -m "docs: explain the claude code hooks"'
expect 0 'git commit -m "refactor: drop copilot dependency"'
expect 0 'git commit -m "feat: answer generated with templates"'
expect 0 'git commit -m "feat: ask Claude to review"'
expect 2 'git commit -m "feat: x

Co-Authored-By: Claude <noreply@anthropic.com>"'
expect 2 'git commit -m "feat: x" -m "Co-authored-by: Copilot <c@x>"'
expect 2 'git commit -m "feat: x" -m "Generated with Claude Code"'
expect 2 'git -C . commit -m "x

Co-Authored-By: Claude <a@b>"'
expect 2 'git commit -m "$(cat <<'"'"'EOF'"'"'
feat: x

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"'
printf 'feat: y\n\nCo-Authored-By: Claude <a@b>\n' > "$tmp/msg.txt"
expect 2 "git commit -F $tmp/msg.txt"
expect 0 'cat <<EOF
git add .
Co-Authored-By: Claude
EOF'

echo "== protected branches"
git checkout -q master
for c in 'git commit -m x' 'git -C . commit -m x' 'git --git-dir=.git commit -m x' 'git commit --amend' 'git -c commit.gpgsign=false commit -m x'; do expect 2 "$c"; done
expect 0 'git switch -c feature/y && git commit -m x'
expect 0 'git checkout -b feature/y; git commit -m x'
expect 0 'git commit-tree HEAD'
expect 0 'git status'
git checkout -q -b develop; expect 2 'git commit -m x'; git checkout -q master; git branch -q -D develop

echo "== pr gate (branch changes code)"
branch feature/code; commit_file "$CODE_FILE"; unmark
for c in 'gh pr create --title x' 'gh pr new' 'gh -R a/b pr create' 'gh --repo a/b pr create' 'GH_TOKEN=1 gh pr create' '/opt/homebrew/bin/gh pr create' \
  'timeout 5 gh pr create' 'xargs gh pr create' 'bash -c "gh pr create"' '(gh pr create)' 'if true; then gh pr create; fi' 'cd x && gh pr create --fill'; do expect 2 "$c"; done
for c in 'gh pr view' 'gh pr list' 'gh pr checks' 'gh pr create-foo' 'gh pr merge 1' 'echo gh pr create'; do expect 0 "$c"; done
expect 2 'git commit -m x && gh pr create'
expect 2 'git add a; git commit -m x; gh pr create'
"$MARK" >/dev/null 2>&1; check 0 "mark-reviewed on a clean tree" "$?"
expect 0 'gh pr create --title x'
commit_file "$CODE_FILE"
guard 'gh pr create'; check 2 "gh pr create after a later code commit" "$?"
"$MARK" >/dev/null 2>&1
mkdir -p "$(dirname "$SPEC_FILE")"; echo spec > "$SPEC_FILE"; git add "$SPEC_FILE"; git commit -q -m "chore: archive"
guard 'gh pr create'; check 0 "gh pr create after a spec-only commit" "$?"
commit_file README.md
guard 'gh pr create'; check 2 "gh pr create after a non-spec commit" "$?"
unmark; expect 0 'spectra archive foo --preview'
if [ "$GATE_ARCHIVE" = 1 ]; then expect 2 'spectra archive foo'; expect 2 'npx spectra archive foo'; else expect 0 'spectra archive foo'; fi
expect 0 'spectra archived'

echo "== pr gate (docs only)"
branch feature/docs; commit_file "$DOC_FILE"; unmark
expect 0 'gh pr create --title x'

echo "== jq missing"
mkdir "$tmp/bin"; for p in git dirname cat awk tr grep sed mkdir basename bash; do ln -s "$(command -v $p)" "$tmp/bin/$p"; done
printf '%s' '{"tool_input":{"command":"git add -A"}}' | PATH="$tmp/bin" /bin/bash "$GUARD" >/dev/null 2>&1; check 1 "guard without jq is a visible non-blocking error" "$?"

echo "== mark-reviewed"
branch feature/code; commit_file "$CODE_FILE"; unmark; mkdir -p "$(dirname "$CODE_FILE")/sub"
(cd "$(dirname "$CODE_FILE")/sub" && env -u CLAUDE_PROJECT_DIR "$MARK" >/dev/null 2>&1); check 0 "mark from a subdirectory" "$?"
[ -f "$tmp/repo/.claude/state/review-passed" ]; check 0 "record written at the repository root" "$?"
[ ! -e "$tmp/repo/$(dirname "$CODE_FILE")/sub/.claude" ]; check 0 "no stray .claude in the subdirectory" "$?"
echo dirty >> "$CODE_FILE"; "$MARK" >/dev/null 2>&1; check 1 "refuses a dirty tree" "$?"; git checkout -q -- "$CODE_FILE"

echo "== stop hook"
stop() { printf '%s' "${1:-{\}}" | "$STOP" 2>/dev/null; }
branch feature/code; commit_file "$CODE_FILE"; unmark
out=$(stop); [ "$(printf '%s' "$out" | jq -r .decision 2>/dev/null)" = "block" ]; check 0 "first stop asks for a review" "$?"
out=$(stop); [ -z "$out" ]; check 0 "second stop at the same HEAD is silent" "$?"
commit_file "$CODE_FILE"
out=$(stop '{"stop_hook_active":true}'); [ -z "$out" ]; check 0 "stop_hook_active is silent" "$?"
"$MARK" >/dev/null 2>&1; out=$(stop); [ -z "$out" ]; check 0 "reviewed HEAD is silent" "$?"
branch feature/docs; commit_file "$DOC_FILE"; unmark
out=$(stop); [ -z "$out" ]; check 0 "docs-only branch is silent" "$?"
git checkout -q master; out=$(stop); [ -z "$out" ]; check 0 "protected branch is silent" "$?"

echo "== remind-docs"
reminder() { printf '%s' "$(jq -n --arg f "$1" '{tool_input:{file_path:$f}}')" | "$DOCS" 2>/dev/null; }
branch feature/code; unmark
out=$(reminder "$tmp/repo/$REMIND_FILE"); [ -n "$out" ]; check 0 "first edit of a documented file reminds" "$?"
out=$(reminder "$tmp/repo/$REMIND_FILE"); [ -z "$out" ]; check 0 "second edit is silent" "$?"
out=$(reminder "$tmp/repo/README.md"); [ -z "$out" ]; check 0 "other files are silent" "$?"

echo
echo "passed: $pass, failed: $fail"
[ "$fail" = 0 ]
