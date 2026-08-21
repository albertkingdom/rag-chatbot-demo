## Why

`HybridRetriever` 的 `_gather_candidates` 步驟目前對每個 BM25 index 中找不到的 `doc_id`，都會呼叫 `_find_in_vector_docs` 重新對 Pinecone 發起一次完整的 `similarity_search`。根據 LangSmith trace，此步驟耗時 5.48 秒，佔整體 retrieve 時間的 75%。而 Vector Search 階段其實已經從 Pinecone 拿回完整的 `Document` 物件，只是回傳時僅保留了 `(doc_id, score)`，完整內容被丟棄。

## What Changes

- `_vector_search` 除了回傳 `(doc_id, score)` 清單外，同時回傳一個 `doc_id → Document` 的查找字典
- `_gather_candidates` 優先從 BM25 index 取得文件，其次從 vector search 的查找字典取得，不再呼叫 `_find_in_vector_docs`
- 移除 `_find_in_vector_docs` 方法

## Non-Goals

- 不改變 BM25 與 Vector Search 的並行化策略（留待後續優化）
- 不調整 RRF fusion 邏輯或 `FUSION_TOP_M` 參數
- 不改變 rerank 階段的行為

## Capabilities

### New Capabilities

（無）

### Modified Capabilities

- `hybrid-retrieval`: `_vector_search` 的回傳值增加 Document 查找字典；`_gather_candidates` 改為使用此字典取代重複的 Pinecone 查詢；移除 `_find_in_vector_docs`

## Impact

- Affected specs: `hybrid-retrieval`（內部實作變更，外部 `retrieve()` 回傳值不變）
- Affected code:
  - Modified: `src/hybrid_retriever.py`
  - Removed: 無獨立檔案刪除，僅移除 `_find_in_vector_docs` 方法
