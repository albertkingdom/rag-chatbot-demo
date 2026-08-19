# agentic-rag-pipeline

Agentic RAG 管線：混合路由（規則 + LLM）決定是否需要 RAG 檢索，檢索後自動評估品質並重試。

## Behavior

### Router（查詢路由）

採用**兩階段混合路由**：規則優先判斷，模糊查詢才 fallback 到 LLM。

#### 階段一：規則路由（零延遲）

根據關鍵字匹配快速判斷明確的 case：

- **→ rag**：查詢包含碳管理相關關鍵字（碳、ESG、溫室氣體、碳足跡、碳排、碳盤查、碳中和、淨零、scope 1/2/3、carbon、emission、GHG 等）
- **→ direct**：查詢為明確的一般問題（打招呼、數學計算、翻譯、程式碼等常見模式）
- **→ uncertain**：無法明確判斷 → 進入階段二

關鍵字清單定義在 `RAG_KEYWORDS` 和 `DIRECT_KEYWORDS` 常數中，方便維護。

#### 階段二：LLM 路由（僅 uncertain 才觸發）

LLM 根據查詢內容和對話歷史判斷路由。使用 structured output（Pydantic model）：

```python
class RouterResult(BaseModel):
    route: Literal["rag", "direct"]
    rewritten_query: str  # 改寫後的查詢，若不需改寫則原樣回傳
    reasoning: str  # 路由理由（用於 logging/debug）
```

#### 查詢改寫

無論哪個階段決定路由，若有對話歷史且為追問，都需要改寫為獨立查詢（取代現有 `rewrite_query`）：

- 規則路由命中 → 僅在有對話歷史時呼叫 LLM 做查詢改寫
- LLM 路由 → 路由判斷與查詢改寫在同一次 LLM call 完成

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

- 規則路由應覆蓋大部分明確 case，減少 LLM 呼叫次數。
- Router 的 LLM 呼叫（僅 uncertain case）使用簡短 prompt 以降低延遲。
- 最多執行 2 次檢索（初始 + 1 次重試），避免延遲過高。
- 品質評估使用 reranker score，不額外呼叫 LLM。
- 所有函式必須為 async 以保持與現有架構一致。
- Guardrails（輸入/輸出）維持在路由之外，不改動。
- Semantic cache 僅用於 RAG 路徑（direct 路徑不需快取，因為沒有檢索成本）。
