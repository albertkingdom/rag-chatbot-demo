# agentic-rag-pipeline

基於 LangGraph 的 Agentic RAG 管線，讓 LLM 自主決定查詢路由與檢索策略。

## Behavior

### State Schema

Agent 使用 TypedDict 管理狀態，在節點間傳遞：

| Field | Type | Description |
|-------|------|-------------|
| query | str | 原始使用者查詢 |
| rewritten_query | str | 改寫後的查詢（若有） |
| history | list | 對話歷史 |
| route | str | 路由決策：`rag` / `direct` / `off_topic` |
| documents | list[Document] | 檢索到的文件 |
| context | str | 格式化後的上下文 |
| sources | list[str] | 參考來源 |
| generation | str | LLM 生成的回答 |
| retry_count | int | 檢索重試次數 |
| grade_passed | bool | 文件品質是否通過 |

### Nodes

1. **router**: 接收 query + history，LLM 判斷路由。使用 structured output 回傳 `{route, rewritten_query}`。若有對話歷史且為追問，同時完成查詢改寫。
2. **retrieve**: 呼叫現有 `HybridRetriever.retrieve()` + `rerank_candidates()`，將結果存入 state。
3. **grade_documents**: LLM 評估檢索結果與查詢的相關性。若 reranker 最高分低於閾值或 LLM 判斷不相關，標記 `grade_passed=False`。
4. **rewrite**: 根據初始查詢和失敗的檢索結果，LLM 改寫查詢以改善檢索。
5. **generate**: 使用現有 QA prompt + context + history 呼叫 LLM 生成回答。

### Edges (Conditional)

```
START → router
router → generate        (route == "direct")
router → off_topic_end   (route == "off_topic")
router → retrieve        (route == "rag")
retrieve → grade_documents
grade_documents → generate           (grade_passed == True)
grade_documents → rewrite            (grade_passed == False && retry_count < 1)
grade_documents → generate           (grade_passed == False && retry_count >= 1)
rewrite → retrieve
generate → END
off_topic_end → END
```

### Streaming

使用 `graph.astream_events(v=2)` 捕捉 generate 節點的 LLM 串流 token，逐 chunk yield 給前端。

### Configuration

| Config | Default | Description |
|--------|---------|-------------|
| AGENT_MAX_RETRIES | 1 | 檢索重試上限 |
| GRADE_SCORE_THRESHOLD | 0.3 | Reranker 最高分低於此值觸發重試 |

## Constraints

- Agent 主循環最多執行 2 次檢索（初始 + 1 次重試），避免無限迴圈。
- Router 的 LLM 呼叫應使用較短的 prompt 以降低延遲。
- 所有節點必須為 async 以保持與現有 async 架構一致。
- Guardrails（輸入/輸出）維持在 agent 之外，不納入 graph 節點。
