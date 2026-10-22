#!/usr/bin/env bash
# PostToolUse hook for Edit|Write: architecture.md describes the RAG flow and parameters,
# so editing the files that define them should be paired with an architecture.md update
# (CLAUDE.md，文件). Reminds once; stays quiet after architecture.md is changed.
set -u

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -z "$file" ] && exit 0

case "$file" in
  */src/chat_service.py|*/src/query_router.py|*/src/hybrid_retriever.py|*/src/bm25_index.py|\
*/src/rerank_stage.py|*/src/retrieval_grader.py|*/src/rag_pipeline.py|*/src/cache_service.py|\
*/src/guardrails.py|*/src/build_vector_store.py|*/src/manual_service.py|*/src/services.py|\
*/src/config.py) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
if git status --porcelain -- architecture.md 2>/dev/null | grep -q .; then
  exit 0
fi

jq -n --arg f "$(basename "$file")" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: ("You edited src/\($f), which architecture.md documents. If behavior, flow or a parameter changed, update architecture.md in the same change (Key Parameters table, request flow or known issues). For a new feature also add one README feature bullet.")
  }
}'
