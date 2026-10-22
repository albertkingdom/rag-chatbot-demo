#!/usr/bin/env bash
# PreToolUse gate for Bash calls. Exit 2 blocks the call and returns stderr to Claude.
#
# Gates:
#   1. git add -A / --all / -u / .  and  git commit -a    stage files by name
#   2. git commit on a protected branch                   work on a feature branch
#   3. git commit with an AI attribution trailer          project rule: no AI attribution in commits
#   4. gh pr create (and spectra archive, if lib.sh says so) without a recorded review of HEAD
#   5. git commit chained with gh pr create               the commit changes HEAD after the check
#
# It is a guard rail against mistakes, not a security boundary: it reads the command text, so
# indirect forms (scripts, aliases, eval) are not seen. The review record is written by
# mark-reviewed.sh. Requires jq.
set -u

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"

if ! command -v jq >/dev/null 2>&1; then
  # Exit 1 is a visible, non-blocking error. Blocking every command would also block installing jq.
  echo "guard-bash.sh: jq is not installed, so the command guards are INACTIVE (brew install jq)." >&2
  exit 1
fi

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$cmd" ] && exit 0

cd "$(root_dir)" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

deny() {
  printf 'BLOCKED by .claude/hooks/guard-bash.sh: %s\n' "$1" >&2
  exit 2
}

# ---- findings, set by scan_command ----------------------------------------------------------
BAD_ADD=0; COMMIT_ALL=0; HAS_COMMIT=0; COMMIT_DIR=""; COMMIT_FILE=""
CREATES_BRANCH=0; PR_GATE=0; ARCHIVE_GATE=0

strip_quotes() { local s=$1; s=${s//\"/}; s=${s//\'/}; printf '%s' "$s"; }

# Remove heredoc bodies so text inside them is not mistaken for commands.
strip_heredocs() {
  awk -v sq="'" '
    skip { t = $0; sub(/^[ \t]+/, "", t); if (t == term) skip = 0; next }
    { print }
    match($0, "<<-?[ \t]*[" sq "\"]?[A-Za-z_][A-Za-z_0-9]*[" sq "\"]?") {
      s = substr($0, RSTART, RLENGTH); gsub("<<-?[ \t]*|[" sq "\"]", "", s); term = s; skip = 1
    }'
}

# skip N tokens safely (shift N fails, and does nothing, when fewer remain)
scan_segment() {
  local depth=$1; shift
  local t prog sub a gdir
  # wrapper phase: skip env assignments, sudo, timeout, xargs, shell keywords, npx and friends
  while [ $# -gt 0 ]; do
    t=$(strip_quotes "$1")
    case "$t" in
      *=*|sudo|env|command|exec|nohup|time|then|do|else|if|while|until|'!'|builtin|xargs|nice|npx|bunx|pnpx) shift ;;
      timeout) shift
        while [ $# -gt 0 ]; do
          case "$(strip_quotes "$1")" in -*|[0-9]*) shift ;; *) break ;; esac
        done ;;
      pnpm|yarn|npm)
        case "$(strip_quotes "${2:-}")" in exec|dlx|x|run) shift 2 ;; *) break ;; esac ;;
      -*) shift ;;
      *) break ;;
    esac
  done
  [ $# -gt 0 ] || return 0
  prog=$(strip_quotes "$1"); prog=${prog##*/}; shift

  case "$prog" in
    bash|sh|zsh)
      # bash -c '<command>': scan the command string too
      while [ $# -gt 0 ]; do
        case "$(strip_quotes "$1")" in
          -c|-lc|-ic|-ec) shift; [ "$depth" -lt 2 ] && scan_command "$*" $((depth + 1)); return 0 ;;
          *) shift ;;
        esac
      done ;;

    git)
      gdir=""
      while [ $# -gt 0 ]; do
        t=$(strip_quotes "$1")
        case "$t" in
          -C) shift; gdir=$(strip_quotes "${1:-}"); [ $# -gt 0 ] && shift ;;
          -c|--git-dir|--work-tree|--namespace) shift; [ $# -gt 0 ] && shift ;;
          -*) shift ;;
          *) break ;;
        esac
      done
      [ $# -gt 0 ] || return 0
      sub=$(strip_quotes "$1"); shift
      case "$sub" in
        add|stage)
          for a in "$@"; do
            a=$(strip_quotes "$a")
            case "$a" in
              .|./|:/|-A|--all|-u|--update) BAD_ADD=1 ;;
              --*) ;;
              -*) case "${a#-}" in *[Au]*) BAD_ADD=1 ;; esac ;;
            esac
          done ;;
        commit)
          HAS_COMMIT=1; COMMIT_DIR=$gdir
          while [ $# -gt 0 ]; do
            a=$(strip_quotes "$1"); shift
            case "$a" in
              -a|--all) COMMIT_ALL=1 ;;
              -F|--file) COMMIT_FILE=$(strip_quotes "${1:-}"); [ $# -gt 0 ] && shift ;;
              --file=*) COMMIT_FILE=${a#--file=} ;;
              --*) ;;
              -?|-??|-???) case "${a#-}" in *a*) COMMIT_ALL=1 ;; esac ;;
            esac
          done ;;
        switch|checkout|branch)
          for a in "$@"; do
            case "$(strip_quotes "$a")" in -c|-C|--create|-b|-B) CREATES_BRANCH=1 ;; esac
          done ;;
      esac ;;

    gh)
      while [ $# -gt 0 ]; do
        case "$(strip_quotes "$1")" in
          -R|--repo|--hostname) shift; [ $# -gt 0 ] && shift ;;
          -*) shift ;;
          *) break ;;
        esac
      done
      if [ "$(strip_quotes "${1:-}")" = "pr" ]; then
        case "$(strip_quotes "${2:-}")" in create|new) PR_GATE=1 ;; esac
      fi ;;

    spectra)
      if [ "$(strip_quotes "${1:-}")" = "archive" ]; then
        ARCHIVE_GATE=1
        for a in "$@"; do
          case "$(strip_quotes "$a")" in --preview) ARCHIVE_GATE=0 ;; esac
        done
      fi ;;
  esac
  return 0
}

scan_command() {
  local text=$1 depth=${2:-0} line
  # split into segments at ; | & ( ) { } and newlines, then look at each one
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    set -f
    # shellcheck disable=SC2086
    set -- $line
    set +f
    scan_segment "$depth" "$@"
  done <<EOT
$(printf '%s\n' "$text" | strip_heredocs | tr ';|&(){}`' '\n')
EOT
}

scan_command "$cmd"

# ---- verdicts -------------------------------------------------------------------------------
[ "$BAD_ADD" = 1 ] && deny "stage files by name (git add <file>); 'git add .', '-A' and '-u' can sweep in .env, local state or unrelated changes."
[ "$COMMIT_ALL" = 1 ] && deny "'git commit -a' stages every tracked change like 'git add -u'. Stage files by name, then commit."

if [ "$HAS_COMMIT" = 1 ]; then
  if [ "$CREATES_BRANCH" = 0 ]; then
    branch=$(git -C "${COMMIT_DIR:-.}" symbolic-ref --short -q HEAD 2>/dev/null || true)
    if [ -n "$branch" ] && is_protected "$branch"; then
      deny "you are on '$branch'. Create feature/<name> first (git switch -c feature/<name>), in a separate command."
    fi
  fi
  text=$cmd
  if [ -n "$COMMIT_FILE" ] && [ -f "$COMMIT_FILE" ]; then text="$text
$(cat "$COMMIT_FILE" 2>/dev/null)"; fi
  if printf '%s' "$text" | grep -Eiq 'co-authored-by:.*(claude|anthropic|openai|copilot|chatgpt|gemini|codex|cursor)|generated (with|by)[^a-z]{0,6}(claude|copilot|chatgpt|codex|gemini)|noreply@anthropic\.com'; then
    deny "commit messages must not carry AI attribution (Co-Authored-By / 'Generated with' lines). Mentioning a tool in the message text is fine; only attribution trailers are blocked."
  fi
fi

if [ "$PR_GATE" = 1 ] || { [ "$ARCHIVE_GATE" = 1 ] && [ "$GATE_ARCHIVE" = 1 ]; }; then
  [ "$HAS_COMMIT" = 1 ] && deny "do not chain 'git commit' with 'gh pr create' / 'spectra archive': the commit changes HEAD after the review check. Run them as separate commands."
  if touches_code && ! reviewed_ok; then
    head_sha=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
    deny "no code review is recorded for HEAD ($head_sha). Run the project-review skill first and resolve blocking and important findings (review-findings skill); it records the result with mark-reviewed.sh. A later commit invalidates the record, except spec/archive-only commits after a review. Branches that change no code (docs and specs only) are exempt."
  fi
fi

exit 0
