#!/usr/bin/env bash
# PostToolUse hook for Edit|Write: architecture.md describes the RAG flow and parameters, so editing
# the files that define them should be paired with an architecture.md update (CLAUDE.md, 文件).
# Reminds once per file and HEAD; stays quiet while architecture.md has uncommitted changes.
# Requires jq.
set -u

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"

command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$file" ] && exit 0

case "$file" in
  */src/chat_service.py|*/src/query_router.py|*/src/hybrid_retriever.py|*/src/bm25_index.py|\
*/src/rerank_stage.py|*/src/retrieval_grader.py|*/src/rag_pipeline.py|*/src/cache_service.py|\
*/src/guardrails.py|*/src/build_vector_store.py|*/src/manual_service.py|*/src/services.py|\
*/src/config.py) ;;
  *) exit 0 ;;
esac

init_repo "$input" || exit 0
if git status --porcelain -- architecture.md 2>/dev/null | grep -q .; then
  exit 0
fi

state="$(root_dir)/.claude/state/docs-reminded"
rel=${file#"$(root_dir)"/}; [ "$rel" = "$file" ] && rel=src/$(basename "$file")
key="$(git rev-parse HEAD 2>/dev/null || echo none):$rel"
if grep -qxF "$key" "$state" 2>/dev/null; then
  exit 0
fi
mkdir -p "$(dirname "$state")"
printf '%s\n' "$key" >> "$state"

jq -n --arg f "$rel" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: ("You edited \($f), which architecture.md documents. If behavior, flow or a parameter changed, update architecture.md in the same change (Key Parameters table, request flow or known issues). For a new feature also add one README feature bullet.")
  }
}'
