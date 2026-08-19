# agentic-rag-pipeline

Agentic RAG 管線：LLM 路由決定是否需要 RAG 檢索，檢索後自動評估品質並重試。

## Behavior

### Router（查詢路由）

LLM 根據查詢內容和對話歷史，一次呼叫完成兩件事：

- **路由判斷**：`rag`（碳管理、ESG、溫室氣體、碳足跡等專業問題）或 `direct`（一般知識問題，LLM 可直接回答）
- **查詢改寫**：若有對話歷史且為追問，改寫為獨立查詢（取代現有 `rewrite_query`）

使用 structured output（Pydantic model）：

```python
class RouterResult(BaseModel):
    route: Literal["rag", "direct"]
    rewritten_query: str  # 改寫後的查詢，若不需改寫則原樣回傳
    reasoning: str  # 路由理由（用於 logging/debug）
```

### Grade Documents（品質評估）

檢索完成後，根據 reranker 分數評估結果品質：

- 取 reranker 回傳的最高分（top-1 score）
- 若最高分 < `GRADE_SCORE_THRESHOLD`，判定品質不足
- 品質不足且未達重試上限 → 改寫查詢後重新檢索
- 品質足夠 or 已達重試上限 → 進入生成

### Retry Rewrite（重試改寫）

當品質評估不通過時，使用 LLM 改寫查詢：

- 輸入：原始查詢 + 檢索到的低品質文件摘要
- 輸出：改寫後的查詢
- 目的：換用不同關鍵字或角度重新搜尋

### 流程

```
query + history
    │
    ▼
  router ──── direct ──→ generate（無 context）
    │
    ├── rag
    ▼
  retrieve → grade_documents ──── 品質不足 & retry < max ──→ rewrite → retrieve
                │
                ├── 品質足夠 → generate（with context）
                └── 已達上限 → generate（用現有結果）
```

### Configuration

| Config | Default | Description |
|--------|---------|-------------|
| RETRIEVAL_MAX_RETRIES | 1 | 檢索重試上限 |
| GRADE_SCORE_THRESHOLD | 0.3 | Reranker top-1 score 低於此值觸發重試 |

## Constraints

- Router 的 LLM 呼叫使用簡短 prompt 以降低延遲。
- 最多執行 2 次檢索（初始 + 1 次重試），避免延遲過高。
- 品質評估使用 reranker score，不額外呼叫 LLM。
- 所有函式必須為 async 以保持與現有架構一致。
- Guardrails（輸入/輸出）維持在路由之外，不改動。
- Semantic cache 僅用於 RAG 路徑（direct 路徑不需快取，因為沒有檢索成本）。
