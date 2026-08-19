## Why

目前系統是碳管理專用助手，所有查詢都走固定 RAG 管線，非碳管理問題直接拒答。產品定位要升級為**通用助手 + 碳管理專業知識**：一般問題 LLM 直接回答，碳管理問題走 RAG 檢索。這需要兩個核心改動：

1. **查詢路由（Router）**：LLM 判斷問題是否需要查碳管理知識庫，取代現有的 intent classifier（只會拒答 off-topic）。
2. **Self-reflective retrieval**：檢索後評估結果品質，不足則改寫查詢重試，避免低品質上下文導致不準確的回答。

## What Changes

- 新增 `src/query_router.py`：LLM 路由模組，判斷查詢走 `rag`（碳管理相關）或 `direct`（一般問題，LLM 直接回答）。使用 structured output（Pydantic model）。同時完成查詢改寫（對話追問場景）。
- 新增 `src/retrieval_grader.py`：檢索結果品質評估（reranker top-1 score）+ 改寫重試邏輯。
- 修改 `src/rag_pipeline.py`：`chat_stream` 改為先走 router 決定路由，再根據路由走 RAG 或直接生成。RAG 路徑加入品質評估與重試。移除 intent classifier 呼叫。移除獨立的 `rewrite_query` 函式（由 router 吸收）。
- 修改 `src/config.py`：新增 `GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES` 設定。
- 移除 `src/intent_classifier.py`：功能由 router 取代。

不使用 LangGraph，以純 Python async 函式實作，不增加新的框架依賴。

## Non-Goals

- 不替換 LLM 模型（仍使用 Gemini 2.5 Flash via OpenRouter）。
- 不替換向量資料庫（仍使用 Pinecone）或 reranker（仍使用 BGE）。
- 不改動文件上傳/同步流程（`build_vector_store.py`、`ui.py`）。
- 不改動 access control、Redis session、guardrails（輸入/輸出防護）、Docker/Terraform 部署架構。
- 不在此 change 引入額外工具（如碳排計算器），僅建立路由框架以便日後擴展。

## Architecture

```
User Query
    │
    ▼
┌─────────────┐
│  Guardrail  │──── injection detected ──→ 拒答
│  (Input)    │    (現有，不改動)
└─────┬───────┘
      │
      ▼
┌─────────────┐
│   Router    │──── direct ──→ Generate (無 context，LLM 直接回答)
│  (LLM決策)  │──── rag ──→ ↓                          (★ 新增)
└─────┬───────┘     同時完成查詢改寫（若為對話追問）
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
│  Generate   │  LLM 生成回答 (with context)
└─────┬───────┘
      │
      ▼
┌─────────────┐
│  Guardrail  │──── PII/injection ──→ 拒答
│  (Output)   │    (現有，不改動)
└─────┬───────┘
      │
      ▼
   Response (streaming)
```

## Impact

- Affected specs: agentic-rag-pipeline
- Affected code:
  - New: `src/query_router.py`（LLM 路由 + 查詢改寫）
  - New: `src/retrieval_grader.py`（品質評估 + 改寫重試邏輯）
  - Modified: `src/rag_pipeline.py`（`chat_stream` 整合 router、品質評估、重試迴圈；移除 intent classifier 呼叫與 `rewrite_query`）
  - Modified: `src/config.py`（新增 `GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES`）
  - Removed: `src/intent_classifier.py`（功能由 router 取代）
  - New: `tests/test_query_router.py`
  - New: `tests/test_retrieval_grader.py`
  - Modified: `tests/test_intent_classifier.py`（移除或改為測試 router）
- Affected dependencies: 無新增依賴

## Decisions

1. **品質評估方式**：使用 reranker top-1 score（零額外 LLM 呼叫，延遲最低）。後續可視需要切換為 LLM 判斷。
2. **重試改寫方式**：新增專用的改寫 prompt（基於原始查詢 + 低品質文件摘要），與 router 中的對話追問改寫用途不同。
3. **Router 實作**：使用 structured output（Pydantic model）回傳 `{route: "rag"|"direct", rewritten_query: str}`，一次 LLM 呼叫同時完成路由判斷與查詢改寫。
4. **框架選擇**：純 Python async 函式，不使用 LangGraph。零新依賴。
