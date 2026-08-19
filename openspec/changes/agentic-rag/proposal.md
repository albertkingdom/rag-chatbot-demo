## Why

目前 RAG 管線是一條固定流水線：每個查詢都經過 輸入防護 → 查詢改寫 → 意圖分類 → 語意快取 → 混合檢索(BM25+Pinecone) → BGE 重排序 → 生成 → 輸出防護。這導致幾個問題：

1. **不必要的檢索開銷**：簡單的寒暄、跟碳管理無關的問題、或是已在對話上下文中回答過的問題，仍然會走完整個檢索流程。目前的 intent classifier 雖然能擋掉 off-topic 問題，但判斷粒度粗且無法區分「不需要 RAG 就能回答」與「需要 RAG」。
2. **無法自我修正**：當初始檢索結果品質差（例如 reranker 分數普遍偏低），系統無法重新調整查詢策略或改用不同搜尋方式，只能將低品質的上下文餵給 LLM 生成不準確的回答。
3. **擴展性受限**：固定管線難以加入新的工具（如碳排計算、表格查詢、跨文件比較），每加一個能力就需要在管線中硬編碼新的分支。

Agentic RAG 讓 LLM 作為 agent 自主決定：是否需要檢索、用什麼策略檢索、檢索結果是否足夠、是否需要重試。這能提升回答品質並為未來擴展工具奠定基礎。

## What Changes

- 新增 LangGraph StateGraph 作為 agent 主循環，取代現有 `rag_pipeline.py` 中的固定流水線。
- 將現有混合檢索 + 重排序封裝為 agent 可呼叫的 `retrieve` tool。
- 新增 `router` 節點：LLM 根據查詢內容決定路由（直接回答 / RAG 檢索 / off-topic 拒答）。
- 新增 `grade_documents` 節點：LLM 評估檢索結果品質，若不足則改寫查詢重新檢索（最多重試 1 次）。
- 移除獨立的 `intent_classifier.py` 呼叫，其功能由 router 節點吸收。
- 移除獨立的 `rewrite_query` 函式，改寫邏輯由 agent 在需要時自行執行。
- 保留現有的 guardrails（輸入/輸出防護）、semantic cache、chat history、MongoDB logging。
- 保留串流回應（streaming）能力，使用 LangGraph 的 astream_events。

## Non-Goals

- 不替換 LLM 模型（仍使用 Gemini 2.5 Flash via OpenRouter）。
- 不替換向量資料庫（仍使用 Pinecone）或 reranker（仍使用 BGE）。
- 不改動文件上傳/同步流程（`build_vector_store.py`、`ui.py` 的上傳功能）。
- 不在此 change 引入額外工具（如碳排計算器），僅建立 agent 框架以便日後擴展。
- 不改動 access control、Redis session、Docker/Terraform 部署架構。

## Capabilities

### New Capabilities

- `agentic-rag-pipeline`: 基於 LangGraph 的 agent 主循環，包含 router（查詢路由）、retrieve（檢索工具）、grade_documents（結果品質評估）、generate（生成回答）等節點，取代固定管線。

### Modified Capabilities

- `hybrid-retrieval`: 不改動檢索邏輯本身，但改為被 agent 的 retrieve tool 呼叫，而非直接嵌入管線。

### Removed Capabilities

- `intent-classification`（獨立模組）：功能由 agent router 節點吸收，`intent_classifier.py` 將被移除。

## Architecture

```
User Query
    │
    ▼
┌─────────────┐
│  Guardrail  │──── injection detected ──→ 拒答
│  (Input)    │
└─────┬───────┘
      │
      ▼
┌─────────────┐
│   Router    │──── off-topic ──→ 拒答訊息
│  (LLM決策)  │──── 可直接回答 ──→ Generate (無 context)
│             │──── 需要RAG ──→ ↓
└─────┬───────┘
      │
      ▼
┌─────────────┐
│  Retrieve   │  混合檢索 + 重排序 (現有 hybrid_retriever + rerank_stage)
└─────┬───────┘
      │
      ▼
┌──────────────┐
│   Grade      │──── 品質不足 & 未重試 ──→ 改寫查詢 → Retrieve
│  Documents   │──── 品質足夠 or 已重試 ──→ ↓
└─────┬────────┘
      │
      ▼
┌─────────────┐
│  Generate   │  LLM 生成回答
└─────┬───────┘
      │
      ▼
┌─────────────┐
│  Guardrail  │──── PII/injection ──→ 拒答
│  (Output)   │
└─────┬───────┘
      │
      ▼
   Response (streaming)
```

## Impact

- Affected specs: agentic-rag-pipeline, hybrid-retrieval, intent-classification
- Affected code:
  - New: `src/agent_graph.py`（LangGraph StateGraph 定義、節點實作）
  - Modified: `src/rag_pipeline.py`（`chat_stream` 改為呼叫 agent graph；移除 `rewrite_query`、移除 intent classifier 呼叫；保留 guardrails/cache/logging 邏輯）
  - Modified: `src/services.py`（新增 agent graph singleton provider）
  - Modified: `src/config.py`（新增 agent 相關設定：max retries、grading threshold）
  - Removed: `src/intent_classifier.py`（功能由 agent router 吸收）
  - New: `tests/test_agent_graph.py`
  - Modified: `tests/test_intent_classifier.py`（移除或改為測試 router 節點）
- Affected dependencies: 新增 `langgraph` 至 `requirements.in` / `requirements.txt`

## Open Questions

1. Router 節點的路由決策應使用 structured output（Pydantic model）還是 function calling？前者與現有 intent classifier 一致，後者更符合 agent 慣例。
2. Grade documents 的品質閾值如何設定？可考慮用 reranker score 的平均值或 LLM 判斷。
3. Semantic cache 應在 agent 之前還是之後？之前可省去 agent 呼叫成本，但可能快取到不同路由的結果。
