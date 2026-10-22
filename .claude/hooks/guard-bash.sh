#!/usr/bin/env bash
# PreToolUse gate for Bash calls. Exit 2 blocks the call and returns stderr to Claude.
#
# Gates:
#   1. git add -A / --all / -u / .  and  git commit -a    stage files by name
#   2. git commit on a protected branch                   work on a feature branch
#   3. git commit with an AI attribution line             project rule: no AI attribution in commits
#   4. gh pr create (and spectra archive, if lib.sh says so) without a recorded review of HEAD
#   5. git commit chained with gh pr create               the commit changes HEAD after the check
#
# It is a guard rail against mistakes, not a security boundary: it reads the command text, so
# indirect forms (scripts, aliases, eval) are not seen. Quotes are respected, so text inside a
# commit message is not mistaken for a command. Commands longer than 30000 characters are only
# scanned up to that length. The review record is written by mark-reviewed.sh. Requires jq.
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

init_repo "$input" || exit 0

deny() {
  printf 'BLOCKED by .claude/hooks/guard-bash.sh: %s\n' "$1" >&2
  exit 2
}

RS=$'\x1e'; US=$'\x1f'; NL=$'\n'; TAB=$'\t'

# ---- findings, set by scan_command ----------------------------------------------------------
BAD_ADD=0; COMMIT_ALL=0; HAS_COMMIT=0; COMMIT_DIR=""; COMMIT_FILE=""; MSG_TEXT=""
CREATES_BRANCH=0; PR_GATE=0; ARCHIVE_GATE=0

# Remove heredoc bodies so text inside them is not mistaken for commands. A '<<' only starts a
# heredoc when it is not part of '<<<' or '$(( ... ))', is not quoted text like "a << b", and a
# terminator line really follows; otherwise the text is left alone.
strip_heredocs() {
  awk -v sq="'" '
    function heredoc_term(s,    pos, st, m, prev, pre, ok, q, n1, n2, t) {
      pos = 1
      while (match(substr(s, pos), "<<-?[ \t]*[" sq "\"]?[A-Za-z_][A-Za-z_0-9]*[" sq "\"]?")) {
        st = pos + RSTART - 1
        m = substr(s, st, RLENGTH)
        prev = (st > 1) ? substr(s, st - 1, 1) : ""
        pre = substr(s, 1, st - 1)
        ok = 1
        if (prev == "<") ok = 0
        if (pre ~ /\(\([^)]*$/) ok = 0
        if (m ~ /^<<-?[ \t]/) {
          q = pre; n1 = gsub(/"/, "", q)
          q = pre; n2 = gsub(sq, "", q)
          if ((n1 % 2) == 1 || (n2 % 2) == 1) ok = 0
        }
        if (ok) { t = m; gsub("<<-?[ \t]*|[" sq "\"]", "", t); return t }
        pos = st + RLENGTH
      }
      return ""
    }
    { line[NR] = $0 }
    END {
      i = 1
      while (i <= NR) {
        print line[i]
        term = heredoc_term(line[i])
        if (term != "") {
          for (j = i + 1; j <= NR; j++) { t = line[j]; sub(/^[ \t]+/, "", t); if (t == term) break }
          if (j <= NR) { i = j + 1; continue }
        }
        i++
      }
    }'
}

# Split a command into segments (at ; | & ( ) { } backtick and newline outside quotes) and
# words (at spaces outside quotes), removing quotes and backslashes. Result in TOKOUT: words
# separated by $US, segments by $RS.
tokenize() {
  local s=$1 n i c q="" tok="" has=0
  TOKOUT=""
  n=${#s}
  for ((i = 0; i < n; i++)); do
    c=${s:i:1}
    if [ -n "$q" ]; then
      if [ "$c" = "$q" ]; then
        q=""
      elif [ "$q" = '"' ] && [ "$c" = '\' ] && [[ ${s:i+1:1} == [\"\\\$\`] ]]; then
        i=$((i + 1)); tok+=${s:i:1}
      else
        tok+=$c
      fi
      continue
    fi
    case "$c" in
      "'"|'"') q=$c; has=1 ;;
      '\')
        i=$((i + 1)); c=${s:i:1}
        if [ "$c" = "$NL" ]; then
          if [ "$has" = 1 ]; then TOKOUT+="$tok$US"; tok=""; has=0; fi
        else
          tok+=$c; has=1
        fi ;;
      ' '|"$TAB")
        if [ "$has" = 1 ]; then TOKOUT+="$tok$US"; tok=""; has=0; fi ;;
      "$NL"|';'|'|'|'&'|'('|')'|'{'|'}'|'`')
        if [ "$has" = 1 ]; then TOKOUT+="$tok$US"; tok=""; has=0; fi
        case "$TOKOUT" in ""|*"$RS") ;; *) TOKOUT+=$RS ;; esac ;;
      *) tok+=$c; has=1 ;;
    esac
  done
  if [ "$has" = 1 ]; then TOKOUT+="$tok$US"; fi
}

scan_segment() {
  local depth=$1; shift
  local prog sub a c rest val gdir=""
  # wrapper phase: skip env assignments, sudo, timeout, xargs, shell keywords, npx and friends
  while [ $# -gt 0 ]; do
    case "$1" in
      *=*|sudo|env|command|exec|nohup|time|then|do|else|if|while|until|'!'|builtin|xargs|nice|npx|bunx|pnpx) shift ;;
      timeout) shift
        while [ $# -gt 0 ]; do
          case "$1" in -*|[0-9]*) shift ;; *) break ;; esac
        done ;;
      pnpm|yarn|npm)
        case "${2:-}" in exec|dlx|x|run) shift 2 ;; *) break ;; esac ;;
      -n|-I|-P|-L|-d|-E|-s|-u|-C) [ $# -ge 2 ] && shift 2 || shift ;;
      -*) shift ;;
      *) break ;;
    esac
  done
  [ $# -gt 0 ] || return 0
  prog=${1##*/}; shift

  case "$prog" in
    bash|sh|zsh)
      # bash -c '<command>': scan the command string too
      while [ $# -gt 0 ]; do
        case "$1" in
          -c*|-[a-zA-Z]*c*)
            [ "$depth" -lt 2 ] && [ -n "${2:-}" ] && scan_command "$2" $((depth + 1))
            return 0 ;;
          *) shift ;;
        esac
      done ;;

    git)
      while [ $# -gt 0 ]; do
        case "$1" in
          -C) gdir=${2:-}; [ $# -ge 2 ] && shift 2 || shift ;;
          -c|--git-dir|--work-tree|--namespace) [ $# -ge 2 ] && shift 2 || shift ;;
          -*) shift ;;
          *) break ;;
        esac
      done
      [ $# -gt 0 ] || return 0
      sub=$1; shift
      case "$sub" in
        add|stage)
          for a in "$@"; do
            case "$a" in
              .|./|:/|-A|--all|-u|--update) BAD_ADD=1 ;;
              --*) ;;
              -*) case "${a#-}" in *[Au]*) BAD_ADD=1 ;; esac ;;
            esac
          done ;;
        commit)
          HAS_COMMIT=1; COMMIT_DIR=$gdir
          while [ $# -gt 0 ]; do
            a=$1; shift
            case "$a" in
              --all) COMMIT_ALL=1 ;;
              --message|--reuse-message|--reedit-message|--author|--date|--trailer|--template|--cleanup|--fixup|--squash)
                if [ $# -gt 0 ]; then MSG_TEXT="$MSG_TEXT$NL$1"; shift; fi ;;
              --file) COMMIT_FILE=${1:-}; [ $# -gt 0 ] && shift ;;
              --file=*) COMMIT_FILE=${a#--file=} ;;
              --message=*|--trailer=*) MSG_TEXT="$MSG_TEXT$NL${a#*=}" ;;
              --*) ;;
              -?*)
                # short flag group such as -am "x": a = stage all; m F C c t take a value
                rest=${a#-}
                while [ -n "$rest" ]; do
                  c=${rest:0:1}; rest=${rest:1}
                  case "$c" in
                    a) COMMIT_ALL=1 ;;
                    m|F|C|c|t)
                      val=$rest; rest=""
                      if [ -z "$val" ] && [ $# -gt 0 ]; then val=$1; shift; fi
                      if [ "$c" = F ]; then COMMIT_FILE=$val; else MSG_TEXT="$MSG_TEXT$NL$val"; fi ;;
                    S|u) rest="" ;;
                  esac
                done ;;
            esac
          done ;;
        switch)
          # creating a branch, or moving to another non-protected branch, before the commit
          if [ "$HAS_COMMIT" = 0 ]; then
            for a in "$@"; do
              case "$a" in
                -c|-C|--create|--force-create) CREATES_BRANCH=1 ;;
                -*) ;;
                *) is_protected "$a" || CREATES_BRANCH=1 ;;
              esac
            done
          fi ;;
        checkout)
          if [ "$HAS_COMMIT" = 0 ]; then
            for a in "$@"; do
              case "$a" in -b|-B) CREATES_BRANCH=1 ;; esac
            done
          fi ;;
      esac ;;

    gh)
      while [ $# -gt 0 ]; do
        case "$1" in
          -R|--repo|--hostname) [ $# -ge 2 ] && shift 2 || shift ;;
          -*) shift ;;
          *) break ;;
        esac
      done
      if [ "${1:-}" = "pr" ]; then
        case "${2:-}" in create|new) PR_GATE=1 ;; esac
      fi ;;

    spectra)
      if [ "${1:-}" = "archive" ]; then
        ARCHIVE_GATE=1
        for a in "$@"; do
          case "$a" in --preview) ARCHIVE_GATE=0 ;; esac
        done
      fi ;;
  esac
  return 0
}

scan_command() {
  local text=$1 depth=${2:-0} rest seg r2 tok
  local -a toks
  text=$(printf '%s' "$text" | strip_heredocs)
  [ "${#text}" -gt 30000 ] && text=${text:0:30000}
  tokenize "$text"
  rest=$TOKOUT
  while [ -n "$rest" ]; do
    seg=${rest%%"$RS"*}
    case "$rest" in *"$RS"*) rest=${rest#*"$RS"} ;; *) rest="" ;; esac
    toks=(); r2=$seg
    while [ -n "$r2" ]; do
      tok=${r2%%"$US"*}
      case "$r2" in *"$US"*) r2=${r2#*"$US"} ;; *) r2="" ;; esac
      toks+=("$tok")
    done
    if [ "${#toks[@]}" -gt 0 ]; then scan_segment "$depth" "${toks[@]}"; fi
  done
}

# AI attribution lines: a trailer or footer that credits an AI tool. Mentioning a tool in the
# message text is not attribution. Anchored to the start of a line.
has_attribution() {
  printf '%s\n' "$1" | grep -Eiq '^[[:space:]]*co-authored-by:.*(claude|anthropic|openai|copilot|chatgpt|gemini|codex|cursor)|^[[:space:]]*(🤖[[:space:]]*)?generated (with|by)[[:space:]]+\[?(claude|copilot|chatgpt|codex|gemini|cursor)|noreply@anthropic\.com'
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
  text="$MSG_TEXT
$cmd"
  if [ -n "$COMMIT_FILE" ] && [ -f "$COMMIT_FILE" ]; then text="$text
$(cat "$COMMIT_FILE" 2>/dev/null)"; fi
  if has_attribution "$text"; then
    deny "commit messages must not carry an AI attribution line (Co-Authored-By: <AI tool>, 'Generated with <AI tool>'). Mentioning a tool in the message text is fine; only attribution lines are blocked."
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
