## Why

目前 RAG 管線是一條固定流水線：每個查詢都經過 輸入防護 → 查詢改寫 → 意圖分類 → 語意快取 → 混合檢索(BM25+Pinecone) → BGE 重排序 → 生成 → 輸出防護。當初始檢索結果品質差（例如 reranker 分數普遍偏低），系統無法重新調整查詢策略，只能將低品質的上下文餵給 LLM 生成不準確的回答。

Self-reflective retrieval 在檢索後加入品質評估，若結果不足則自動改寫查詢重試，提升回答品質。

## What Changes

- 新增 `src/retrieval_grader.py`：檢索結果品質評估模組，根據 reranker 分數判斷檢索品質是否足夠。
- 修改 `src/rag_pipeline.py`：在檢索與生成之間插入品質評估步驟。若品質不足，改寫查詢並重新檢索（最多重試 1 次），再進入生成。
- 修改 `src/config.py`：新增品質評估相關設定（`GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES`）。

不使用 LangGraph，以純 Python async 函式實作，不增加新的框架依賴。

## Non-Goals

- 不替換 LLM 模型（仍使用 Gemini 2.5 Flash via OpenRouter）。
- 不替換向量資料庫（仍使用 Pinecone）或 reranker（仍使用 BGE）。
- 不改動文件上傳/同步流程（`build_vector_store.py`、`ui.py`）。
- 不移除或替換現有的 intent classifier — off-topic 過濾維持原樣。
- 不移除或替換現有的 query rewriting — 對話追問改寫維持原樣。
- 不新增 router 節點或查詢路由機制。
- 不改動 access control、Redis session、guardrails、semantic cache、Docker/Terraform 部署架構。

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
┌──────────────┐
│ Query Rewrite│  (現有，對話追問時改寫為獨立查詢)
└─────┬────────┘
      │
      ▼
┌───────────────┐
│    Intent     │──── off-topic ──→ 拒答訊息
│  Classifier   │    (現有，不改動)
└─────┬─────────┘
      │
      ▼
┌──────────────┐
│ Semantic     │──── cache hit ──→ 回傳快取回答
│ Cache        │    (現有，不改動)
└─────┬────────┘
      │
      ▼
┌─────────────┐
│  Retrieve   │  混合檢索 + 重排序 (現有，不改動)
└─────┬───────┘
      │
      ▼
┌──────────────┐
│    Grade     │──── 品質不足 & 未重試 ──→ 改寫查詢 → Retrieve
│  Documents   │──── 品質足夠 or 已重試 ──→ ↓       (★ 新增)
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

- Affected specs: agentic-rag-pipeline
- Affected code:
  - New: `src/retrieval_grader.py`（品質評估 + 改寫重試邏輯）
  - Modified: `src/rag_pipeline.py`（`chat_stream` 在檢索後加入品質評估與重試迴圈）
  - Modified: `src/config.py`（新增 `GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES`）
  - New: `tests/test_retrieval_grader.py`
- Affected dependencies: 無新增依賴

## Open Questions

1. 品質評估方式：用 reranker score 的最高分 vs 平均分 vs LLM 判斷？reranker score 不需額外 LLM 呼叫，延遲最低；LLM 判斷更準但增加一次呼叫。建議先用 reranker score，後續可切換。
2. 重試時的查詢改寫：復用現有的 `rewrite_query` 邏輯（基於對話歷史改寫）還是新增一個針對檢索失敗的改寫 prompt（基於原始查詢 + 失敗原因改寫）？建議後者，因為目的不同。
