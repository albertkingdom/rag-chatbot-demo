# agentic-rag-pipeline

Self-reflective retrieval：在檢索後評估結果品質，不足則改寫查詢重試，提升回答準確度。

## Behavior

### 品質評估（Grade Documents）

檢索完成後，根據 reranker 分數評估結果品質：

- 取 reranker 回傳的最高分（top-1 score）
- 若最高分 < `GRADE_SCORE_THRESHOLD`，判定品質不足
- 品質不足且未達重試上限 → 改寫查詢後重新檢索
- 品質足夠 or 已達重試上限 → 進入生成

### 查詢改寫（Retry Rewrite）

當品質評估不通過時，使用 LLM 改寫查詢以改善檢索結果：

- 輸入：原始查詢 + 檢索到的低品質文件摘要
- 輸出：改寫後的查詢
- 目的：換用不同關鍵字或角度重新搜尋，與現有的 `rewrite_query`（對話追問改寫）目的不同

### 流程

```
retrieve (現有)
    │
    ▼
grade_documents ──── 品質不足 & retry_count < max ──→ rewrite_query_for_retry → retrieve
    │
    ├── 品質足夠 → generate
    └── 已達重試上限 → generate（用現有結果，盡力回答）
```

### Configuration

| Config | Default | Description |
|--------|---------|-------------|
| RETRIEVAL_MAX_RETRIES | 1 | 檢索重試上限 |
| GRADE_SCORE_THRESHOLD | 0.3 | Reranker top-1 score 低於此值觸發重試 |

## Constraints

- 最多執行 2 次檢索（初始 + 1 次重試），避免延遲過高。
- 品質評估使用 reranker score，不額外呼叫 LLM，保持低延遲。
- 改寫重試時的 LLM 呼叫使用簡短 prompt 以降低延遲。
- 所有函式必須為 async 以保持與現有架構一致。
- 不改動現有的 intent classifier、query rewriting、guardrails、cache 邏輯。
